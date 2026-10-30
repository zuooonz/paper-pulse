#!/usr/bin/env python3
"""
按当前领域配置批量检索入库论文

多源检索 + 自动去重 + 元数据补全 + 批量入库
支持按年份分片，绕过 PubMed 10k 返回限制。

数据源:
  1. PubMed (ESearch/EFetch)     — 主源，按年份分片
  2. Europe PMC                  — 补充，分页游标
  3. OpenAlex                    — 补充，学术图谱
  4. Semantic Scholar (可选)      — 通过 paper-search-mcp

用法:
  python scripts/search_domain_papers.py --dry-run             # 仅检索预览
  python scripts/search_domain_papers.py --years 2020-2025      # 指定年份范围
  python scripts/search_domain_papers.py --source pubmed,epmc   # 指定数据源
  python scripts/search_domain_papers.py --auto-ingest          # 跳过审批直接入库
"""
import logging

import argparse
import csv
import sys
import time
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)
# ------------------------------------------------------------------
# Path setup
# ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from sqlalchemy import select
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.crud import upsert_paper
from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.doi import normalize_doi
from core.crossref_api import fetch_metadata_by_doi
from core.domain_config import load_domain
from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
NCBI_API_KEY = configured_value("NCBI_API_KEY", "ncbi", "api_key") or None
NCBI_EMAIL = configured_value("NCBI_EMAIL", "ncbi", "email")
CONTACT_EMAIL = configured_value("CONTACT_EMAIL", "http", "contact_email")
EUROPE_PMC_BASE_URL = api_endpoint("EUROPE_PMC_BASE_URL", "europe_pmc_base_url")
OPENALEX_BASE_URL = api_endpoint("OPENALEX_BASE_URL", "openalex_base_url")
DOMAIN_CONFIG = load_domain()

RATE_LIMIT = {
    "pubmed": 0.11 if NCBI_API_KEY else 0.34,
    "epmc": 0.35,
    "openalex": 0.11,
    "crossref": 0.5,
    "semantic": 0.11,
}

TITLE_SIM_THRESHOLD = 0.85

# ------------------------------------------------------------------
# Search and normalization helpers
# ------------------------------------------------------------------

def normalize_title(title: str) -> str:
    if not title:
        return ""
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _safe_doi(raw: str) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    for prefix in [
        "https://doi.org/", "http://doi.org/",
        "http://dx.doi.org/", "https://dx.doi.org/",
    ]:
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    doi = normalize_doi(raw)
    return doi if doi else None


def _extract_year(pubdate: str) -> Optional[int]:
    if not pubdate:
        return None
    match = re.match(r"(\d{4})", pubdate.strip())
    return int(match.group(1)) if match else None


def verify_relevance(
    title: str,
    journal: str,
    abstract: str = "",
    domain: Optional[Dict] = None,
) -> str:
    """Check relevance using keywords and exclusions from the active domain package."""
    active_domain = domain or DOMAIN_CONFIG
    text = f"{title} {journal} {abstract}".casefold()
    search_cfg = active_domain.get("search") or {}
    if any(str(term).casefold() in text for term in search_cfg.get("exclude_terms", [])):
        return "low"
    terms = [str(term).casefold() for term in active_domain.get("relevance_keywords", []) if str(term).strip()]
    if any(term in text for term in terms):
        title_abstract = f"{title} {abstract}".casefold()
        return "high" if any(term in title_abstract for term in terms) else "medium"
    return "low"


# ------------------------------------------------------------------
# Existing DB deduplication
# ------------------------------------------------------------------

def load_existing_papers(workspace: str) -> Tuple[Set[str], List[Tuple[str, str]]]:
    """Load existing DOIs and titles from DB for deduplication."""
    dois = set()
    titles = []
    with get_session(workspace) as session:
        for row in session.execute(
            select(Paper.doi, Paper.title).where(Paper.title.isnot(None))
        ):
            doi_val = (row[0] or "").strip().lower()
            title_val = row[1] or ""
            if doi_val:
                dois.add(doi_val)
            if title_val:
                titles.append((doi_val, title_val))
    return dois, titles


def deduplicate_results(
    all_results: List[Dict],
    existing_dois: Set[str],
    existing_titles: List[Tuple[str, str]],
) -> List[Dict]:
    """Cross-source dedup + DB dedup + relevance filter."""

    # Layer 1: Cross-source DOI merge
    seen_dois: Dict[str, Dict] = {}
    no_doi: List[Dict] = []

    for r in all_results:
        doi = r.get("doi")
        if doi:
            doi_lower = doi.strip().lower()
            if doi_lower not in seen_dois:
                r["_sources"] = {r["source"]}
                seen_dois[doi_lower] = r
            else:
                existing = seen_dois[doi_lower]
                existing["_sources"].add(r["source"])
                if not existing.get("pmid") and r.get("pmid"):
                    existing["pmid"] = r["pmid"]
                if not existing.get("journal") and r.get("journal"):
                    existing["journal"] = r["journal"]
                if not existing.get("year") and r.get("year"):
                    existing["year"] = r["year"]
                if not existing.get("abstract") and r.get("abstract"):
                    existing["abstract"] = r["abstract"]
        else:
            no_doi.append(r)

    # Layer 2: Filter against DB by DOI
    new_by_doi = {}
    dup_doi_count = 0
    for doi_lower, r in seen_dois.items():
        if doi_lower in existing_dois:
            dup_doi_count += 1
        else:
            new_by_doi[doi_lower] = r

    # Layer 3: Filter against DB by title similarity
    dup_title_count = 0
    still_new = {}
    for doi_lower, r in new_by_doi.items():
        candidate_title = r.get("title", "")
        matched = False
        for db_doi, db_title in existing_titles:
            if title_similarity(candidate_title, db_title) >= TITLE_SIM_THRESHOLD:
                matched = True
                dup_title_count += 1
                break
        if not matched:
            still_new[doi_lower] = r
    new_by_doi = still_new

    # Layer 4: No-DOI dedup against DB + new candidates
    new_no_doi = []
    all_known_titles = [(d, r["title"]) for d, r in new_by_doi.items()]
    all_known_titles.extend(existing_titles)
    for r in no_doi:
        is_dup = any(
            title_similarity(r["title"], t) >= TITLE_SIM_THRESHOLD
            for _, t in all_known_titles
        )
        if not is_dup:
            r["_sources"] = {r["source"]}
            new_no_doi.append(r)
            all_known_titles.append(("", r["title"]))
        else:
            dup_title_count += 1

    # Layer 5: Relevance verification
    verified = []
    filtered_out = 0
    for r in list(new_by_doi.values()) + new_no_doi:
        relevance = verify_relevance(
            r.get("title", ""),
            r.get("journal", ""),
            r.get("abstract", ""),
        )
        if relevance == "high":
            r["_confidence"] = "high"
            verified.append(r)
        elif relevance == "medium":
            r["_confidence"] = "medium"
            r["_needs_review"] = True
            verified.append(r)
        else:
            filtered_out += 1

    print(f"\n{'─' * 60}")
    print("📊 去重 + 验证统计:")
    print(f"  原始结果 (多源合计):       {len(all_results)}")
    print(f"  跨平台 DOI 去重后:         {len(seen_dois) + len(no_doi)}")
    print(f"  数据库已有 (DOI 匹配):     {dup_doi_count}")
    print(f"  数据库已有 (标题匹配):     {dup_title_count}")
    high_cnt = sum(1 for r in verified if r.get("_confidence") == "high")
    med_cnt = sum(1 for r in verified if r.get("_confidence") == "medium")
    print(f"  高可信 ({DOMAIN_CONFIG.get('short_name', '领域')}核心词):     {high_cnt}")
    print(f"  中可信 (需人工复核):       {med_cnt}")
    print(f"  已过滤 (非当前领域):      {filtered_out}")
    print(f"  ✅ 候选论文总计:           {len(verified)}")
    print(f"{'─' * 60}")

    return verified


# ------------------------------------------------------------------
# Source 0: Google Scholar — complementary to PubMed
# ------------------------------------------------------------------

def search_google_scholar(year_range, max_results: int = 200) -> List[Dict]:
    """Search Google Scholar using the active domain's configured queries.

    Google Scholar sorts by relevance/citations, not recency.
    We search broadly and let the DB dedup logic find new papers.
    The year_range is used only for logging context.
    """
    from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher

    searcher = GoogleScholarSearcher(max_retries=3, retry_delay=3.0)

    queries = (DOMAIN_CONFIG.get("search") or {}).get("gs_queries") or []
    exclusions = " ".join(f'-"{term}"' for term in (DOMAIN_CONFIG.get("search") or {}).get("exclude_terms", []))
    print(f"\n🔍 Google Scholar: {len(queries)} configured query(s)")
    print(f"   最大结果数: {max_results} (按引用排序，不限年份)")

    papers = []
    try:
        raw = []
        per_query = max(1, max_results // max(1, len(queries)))
        for query in queries:
            raw.extend(searcher.search(f"{query} {exclusions}".strip(), max_results=per_query))
    except Exception as e:
        print(f"   ⚠️ Google Scholar 搜索失败: {e}")
        return []

    for r in raw:
        # Clean DOI
        doi = (r.doi or "").strip()
        if doi:
            doi = re.sub(r'/(?:full|pdf|abstract|meta|epub)$', '', doi)
            doi = normalize_doi(doi)

        title = (r.title or "").replace('[PDF]', '').replace('[HTML]', '').strip()

        year = r.published_date.year if r.published_date else None

        # Drop results that do not match the active domain keywords.
        relevance = verify_relevance(title, "", r.abstract or "")
        if relevance == "low":
            url_lower = (r.url or "").lower()
            terms = DOMAIN_CONFIG.get("relevance_keywords", [])
            if not any(str(kw).casefold() in url_lower for kw in terms):
                continue

        papers.append({
            "title": title,
            "doi": doi,
            "pmid": "",
            "year": year,
            "journal": "",
            "abstract": r.abstract or "",
            "source": "google_scholar",
            "url": r.url or "",
            "_gs_confidence": relevance,
        })

    print(f"   匹配: {len(papers)} 篇 (过滤后)")
    return papers


# ------------------------------------------------------------------
# Source 1: PubMed — Year-sharded search
# ------------------------------------------------------------------

def search_pubmed_year(year: int, max_results: int = 10000) -> List[Dict]:
    """Search PubMed for a single year (bypasses 10k total limit)."""
    query = f"{(DOMAIN_CONFIG.get('search') or {}).get('pubmed_query', '')} AND {year}[PDAT]"

    if not NCBI_BASE_URL:
        warn_missing("PubMed search", "NCBI_BASE_URL")
        return [], 0
    url = f"{NCBI_BASE_URL}/esearch.fcgi"
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": max_results}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL

    try:
        resp = requests.get(url, params=params, timeout=60, headers={"User-Agent": contact_user_agent()})
        resp.raise_for_status()
        data = resp.json()
        pmids = data.get("esearchresult", {}).get("idlist", [])
        total_count = int(data.get("esearchresult", {}).get("count", 0))
        if total_count > max_results:
            print(f"    ⚠️ {year} 年命中 {total_count} 篇，超过 retmax={max_results}，存在遗漏")
    except Exception as e:
        print(f"    ❌ {year} 年搜索失败: {e}")
        return [], 0

    if not pmids:
        return [], total_count

    # Fetch details via efetch (XML) — includes abstract
    results = []
    batch_size = 500
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        time.sleep(RATE_LIMIT["pubmed"])

        fetch_url = f"{NCBI_BASE_URL}/efetch.fcgi"
        fetch_data = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"}
        if NCBI_API_KEY:
            fetch_data["api_key"] = NCBI_API_KEY
        if NCBI_EMAIL:
            fetch_data["email"] = NCBI_EMAIL
        try:
            resp = requests.post(fetch_url, data=fetch_data, timeout=120, headers={"User-Agent": contact_user_agent()})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            for article in root.findall(".//PubmedArticle"):
                pmid_elem = article.find(".//PMID")
                pmid = pmid_elem.text.strip() if pmid_elem is not None and pmid_elem.text else ""
                if not pmid:
                    continue

                title_elem = article.find(".//ArticleTitle")
                title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""
                if not title:
                    continue

                # DOI
                doi = ""
                doi_elem = article.find('.//ELocationID[@EIdType="doi"]')
                if doi_elem is not None and doi_elem.text:
                    doi = _safe_doi(doi_elem.text)
                if not doi:
                    # Try ArticleIdList
                    for aid in article.findall(".//ArticleId"):
                        if aid.get("IdType") == "doi" and aid.text:
                            doi = _safe_doi(aid.text)
                            break

                # Abstract
                abstract_parts = []
                for abs_elem in article.findall(".//AbstractText"):
                    text = "".join(abs_elem.itertext()).strip()
                    if text:
                        abstract_parts.append(text)
                abstract = " ".join(abstract_parts)

                # Year
                year = None
                year_elem = article.find(".//PubDate/Year")
                if year_elem is not None and year_elem.text:
                    year = _extract_year(year_elem.text)
                if not year:
                    medline_date = article.find(".//PubDate/MedlineDate")
                    if medline_date is not None and medline_date.text:
                        year = _extract_year(medline_date.text)

                # Journal
                journal = ""
                journal_elem = article.find(".//Journal/Title")
                if journal_elem is not None and journal_elem.text:
                    journal = journal_elem.text.strip()

                results.append({
                    "source": "pubmed",
                    "doi": doi,
                    "pmid": pmid,
                    "title": title,
                    "journal": journal,
                    "year": year,
                    "abstract": abstract,
                })
        except Exception as e:
            print(f"    ⚠️ 批次获取失败: {e}")

    return results, total_count


def search_pubmed_range(years: range, max_per_year: int = 10000) -> List[Dict]:
    """Search PubMed year by year."""
    print("\n" + "=" * 60)
    print("🔍 [1/3] PubMed 按年份分片搜索")
    print("=" * 60)
    print(f"  年份范围: {years.start}-{years.stop - 1}")
    print(f"  检索式: {DOMAIN_CONFIG.get('short_name', '领域')}核心词 + 年份限定")

    all_results = []
    total_expected = 0
    for year in years:
        results, count = search_pubmed_year(year, max_per_year)
        total_expected += count
        all_results.extend(results)
        print(f"  📅 {year}: 命中 {count:4d} 篇, 获取 {len(results):4d} 篇 (累计 {len(all_results)})")
        time.sleep(RATE_LIMIT["pubmed"])

    print(f"\n  ✅ PubMed 合计: 预期 {total_expected} 篇, 实际获取 {len(all_results)} 篇")
    return all_results


# ------------------------------------------------------------------
# Source 2: Europe PMC
# ------------------------------------------------------------------

def search_europe_pmc(years: range, max_results: int = 10000) -> List[Dict]:
    """Search Europe PMC with year filter and cursor pagination."""
    print("\n" + "=" * 60)
    print("🔍 [2/3] Europe PMC 搜索")
    print("=" * 60)

    year_filter = ""
    if years:
        year_filter = f" AND (FIRST_PDATE:[{years.start} TO {years.stop - 1}])"
    epmc_query = (DOMAIN_CONFIG.get("search") or {}).get("epmc_query", "")
    query = f"({epmc_query}){year_filter}"
    print(f"  查询: {query[:100]}...")

    if not EUROPE_PMC_BASE_URL:
        warn_missing("Europe PMC", "EUROPE_PMC_BASE_URL")
        return []
    url = f"{EUROPE_PMC_BASE_URL}/search"
    results = []
    cursor_mark = "*"
    page_size = 1000

    while len(results) < max_results:
        params = {
            "query": query, "format": "json", "resultType": "core",
            "pageSize": min(page_size, max_results - len(results)),
            "cursorMark": cursor_mark,
        }
        try:
            time.sleep(RATE_LIMIT["epmc"])
            resp = requests.get(url, params=params, timeout=60, headers={"User-Agent": contact_user_agent()})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️ 获取失败: {e}")
            break

        hits = data.get("resultList", {}).get("result", [])
        if not hits:
            break

        for item in hits:
            doi = _safe_doi(item.get("doi", ""))
            title = (item.get("title") or "").strip()
            if not doi and not title:
                continue
            pmid = item.get("pmid", "") or None
            year_str = item.get("pubYear", "")
            abstract = (item.get("abstractText") or "").strip()
            results.append({
                "source": "epmc",
                "doi": doi,
                "pmid": str(pmid) if pmid else None,
                "title": title,
                "journal": item.get("journalTitle", "") or "",
                "year": int(year_str) if year_str and year_str.isdigit() else None,
                "abstract": abstract,
            })

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor_mark:
            break
        cursor_mark = next_cursor

        if len(results) % 5000 == 0:
            print(f"  ... 已获取 {len(results)} 篇")

    print(f"  ✅ Europe PMC 合计: {len(results)} 篇")
    return results


# ------------------------------------------------------------------
# Source 3: OpenAlex
# ------------------------------------------------------------------

def search_openalex(years: range, max_results: int = 5000) -> List[Dict]:
    """Search OpenAlex with configured domain queries."""
    print("\n" + "=" * 60)
    print("🔍 [3/3] OpenAlex 搜索")
    print("=" * 60)

    query_terms = (DOMAIN_CONFIG.get("search") or {}).get("openalex_queries") or []
    queries = [
        {"filter": f'default.search:"{term}",type:article', "desc": term}
        for term in query_terms
    ]

    year_filter = ""
    if years:
        year_filter = f",from_publication_date:{years.start}-01-01,to_publication_date:{years.stop - 1}-12-31"

    all_results = []
    seen_ids = set()

    for qconf in queries:
        print(f"  检索: {qconf['desc']}")
        page = 1
        per_page = 200

        while len(all_results) < max_results:
            params = {
                "per_page": per_page, "page": page,
                "select": "id,doi,title,publication_year,primary_location,abstract_inverted_index",
                "filter": f"{qconf['filter']}{year_filter}",
            }
            if CONTACT_EMAIL:
                params["mailto"] = CONTACT_EMAIL
            try:
                time.sleep(RATE_LIMIT["openalex"])
                resp = requests.get(
                    f"{OPENALEX_BASE_URL}/works",
                    params=params, timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"    ⚠️ 第 {page} 页失败: {e}")
                break

            items = data.get("results", [])
            if not items:
                break

            for item in items:
                oa_id = item.get("id", "")
                if oa_id in seen_ids:
                    continue
                seen_ids.add(oa_id)

                doi = _safe_doi(item.get("doi", ""))
                title = (item.get("title") or "").strip()
                if not doi and not title:
                    continue

                loc = item.get("primary_location") or {}
                src = loc.get("source") or {}
                journal = src.get("display_name", "") if isinstance(src, dict) else ""

                # Reconstruct abstract from inverted index if available
                abstract = ""
                inv_idx = item.get("abstract_inverted_index")
                if inv_idx:
                    try:
                        word_positions = []
                        for word, positions in inv_idx.items():
                            for pos in positions:
                                word_positions.append((pos, word))
                        word_positions.sort()
                        abstract = " ".join(w for _, w in word_positions)
                    except Exception:
                        logger.warning('处理失败，已跳过该项（search_openalex）', exc_info=True)
                        pass

                all_results.append({
                    "source": "openalex",
                    "doi": doi,
                    "pmid": None,
                    "title": title,
                    "journal": journal,
                    "year": item.get("publication_year"),
                    "abstract": abstract,
                })

            meta = data.get("meta", {})
            total_count = meta.get("count", 0)
            if page == 1:
                print(f"    命中: {total_count} 篇")
            if page * per_page >= total_count:
                break
            page += 1

    print(f"  ✅ OpenAlex 合计: {len(all_results)} 篇")
    return all_results


# ------------------------------------------------------------------
# Source 4: Semantic Scholar (via paper-search-mcp)
# ------------------------------------------------------------------

def search_semantic_scholar(years: range, max_results: int = 1000) -> List[Dict]:
    """Search Semantic Scholar via paper-search-mcp."""
    try:
        from paper_search_mcp.academic_platforms.semantic import SemanticSearcher
    except ImportError:
        print("\n  ⚠️ paper-search-mcp Semantic Scholar 不可用，跳过")
        return []

    print("\n" + "=" * 60)
    print("🔍 [4/4] Semantic Scholar (paper-search-mcp)")
    print("=" * 60)

    searcher = SemanticSearcher()
    query = (DOMAIN_CONFIG.get("search") or {}).get("semantic_query", "")

    try:
        papers = searcher.search(query, max_results=max_results)
    except Exception as e:
        print(f"  ❌ 搜索失败: {e}")
        return []

    results = []
    for p in papers:
        if not p.title:
            continue
        year = p.published_date.year if p.published_date else None
        if years and year and year not in years:
            continue
        results.append({
            "source": "semantic_scholar",
            "doi": _safe_doi(p.doi),
            "pmid": None,
            "title": p.title,
            "journal": "",
            "year": year,
            "abstract": p.abstract or "",
        })

    print(f"  ✅ Semantic Scholar: {len(results)} 篇")
    return results


# ------------------------------------------------------------------
# Ingestion
# ------------------------------------------------------------------

def enrich_from_crossref(doi: str) -> dict:
    """Fetch missing metadata from CrossRef."""
    try:
        meta = fetch_metadata_by_doi(doi)
        if not meta or "_error" in meta:
            return {}
        extra = {}
        if meta.get("title"):
            extra["title"] = meta["title"][0] if isinstance(meta["title"], list) else meta["title"]
        if meta.get("container-title"):
            ct = meta["container-title"]
            extra["journal_name"] = ct[0] if isinstance(ct, list) else ct
        if meta.get("published-print", {}).get("date-parts"):
            parts = meta["published-print"]["date-parts"][0]
            if parts and parts[0]:
                extra["year"] = parts[0]
        elif meta.get("published-online", {}).get("date-parts"):
            parts = meta["published-online"]["date-parts"][0]
            if parts and parts[0]:
                extra["year"] = parts[0]
        if meta.get("abstract"):
            extra["abstract"] = meta["abstract"]
        if meta.get("volume"):
            extra["volume"] = str(meta["volume"])
        if meta.get("issue"):
            extra["issue"] = str(meta["issue"])
        if meta.get("page"):
            extra["pages"] = str(meta["page"])
        extra["doi_meta"] = meta
        return extra
    except Exception:
        logger.warning("论文元数据解析失败", exc_info=True)
        return {}


def ingest_papers(
    papers: List[Dict],
    workspace: str,
    dry_run: bool = False,
    enrich: bool = True,
) -> Dict[str, int]:
    """Ingest deduplicated papers into DB."""
    stats = {"ingested": 0, "enriched": 0, "failed": 0}

    print(f"\n{'=' * 60}")
    if dry_run:
        print("📋 [DRY-RUN] 预览入库结果（不写入数据库）")
    else:
        print(f"📥 正在入库 {len(papers)} 篇论文...")
    print(f"{'=' * 60}")

    if dry_run:
        for i, p in enumerate(papers, 1):
            print(f"  [{i}/{len(papers)}] [DRY] {p['title'][:55]}...")
        return stats

    # Use manual session management with batched commits to avoid SQLite lock
    from paper_library.db import get_session_factory
    session_factory = get_session_factory(workspace)
    session = session_factory()
    batch_size = 100

    try:
        for i, p in enumerate(papers, 1):
            try:
                paper_data = {
                    "title": p.get("title") or "(无标题)",
                    "doi": p.get("doi"),
                    "pmid": p.get("pmid"),
                    "year": p.get("year"),
                    "journal_name": p.get("journal"),
                    "abstract": p.get("abstract"),
                    "is_in_domain": True,
                    "cites_target": False,
                    "source": f"{DOMAIN_CONFIG.get('key', 'domain')}_bulk_import",
                }

                if enrich and p.get("doi"):
                    extra = enrich_from_crossref(p["doi"])
                    if extra:
                        paper_data.update(extra)
                        stats["enriched"] += 1

                paper = upsert_paper(session, paper_data)
                if paper.doi_meta:
                    try:
                        with session.begin_nested():
                            normalize_authors_for_paper(paper.doi_meta, paper.id, session)
                    except Exception as e:
                        print(f"  ⚠️ 作者标准化失败 paper_id={paper.id}: {e}")
                stats["ingested"] += 1
                print(f"  [{i}/{len(papers)}] id={paper.id} {p['title'][:55]}...")

                if i % batch_size == 0:
                    session.commit()
                    print(f"  💾 已提交 {i} 篇")

            except Exception as e:
                stats["failed"] += 1
                print(f"  ❌ 失败: {e}")
                session.rollback()
                # Continue with next paper

        # Final commit for remaining papers
        session.commit()
    finally:
        session.close()

    return stats


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------

def export_csv(papers: List[Dict], filepath: str):
    """Export candidate list to CSV."""
    fieldnames = ["序号", "可信度", "来源", "DOI", "标题", "期刊", "年份", "PMID"]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, p in enumerate(papers, 1):
            writer.writerow({
                "序号": i,
                "可信度": p.get("_confidence", "?"),
                "来源": "+".join(sorted(p.get("_sources", {p.get("source", "?")}))),
                "DOI": p.get("doi", ""),
                "标题": p.get("title", ""),
                "期刊": p.get("journal", ""),
                "年份": p.get("year", ""),
                "PMID": p.get("pmid", ""),
            })
    print(f"\n  💾 CSV 已导出: {filepath}")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    setup_console()
    parser = argparse.ArgumentParser(
        description=f"全{DOMAIN_CONFIG.get('name', '领域')}论文批量检索入库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/search_domain_papers.py --dry-run                   # 预览
  python scripts/search_domain_papers.py --years 2020-2025            # 指定年份
  python scripts/search_domain_papers.py --source pubmed,epmc         # 指定数据源
  python scripts/search_domain_papers.py --auto-ingest                # 直接入库
  python scripts/search_domain_papers.py --output results.csv         # 导出 CSV
        """,
    )
    add_db_arguments(parser)
    parser.add_argument("--years", type=str, default="1995-2026",
                        help="年份范围，如 2020-2025 (默认: 1995-2026)")
    parser.add_argument("--source", type=str, default="pubmed,epmc,openalex",
                        help="数据源列表，逗号分隔 (默认: pubmed,epmc,openalex)")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不写入数据库")
    parser.add_argument("--auto-ingest", action="store_true",
                        help="跳过人工审批直接入库")
    parser.add_argument("--output", type=str, default=None,
                        help="导出 CSV 文件路径")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--max-per-year", type=int, default=10000,
                        help="PubMed 每年最大返回数 (默认 10000)")
    parser.add_argument("--max-epmc", type=int, default=20000,
                        help="Europe PMC 最大返回数 (默认 20000)")
    parser.add_argument("--max-openalex", type=int, default=10000,
                        help="OpenAlex 最大返回数 (默认 10000)")
    parser.add_argument("--max-gs", type=int, default=200,
                        help="Google Scholar 最大返回数 (默认 200)")
    parser.add_argument("--no-enrich", action="store_true",
                        help="跳过 CrossRef 元数据补全")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    # Parse year range
    try:
        start_year, end_year = map(int, args.years.split("-"))
        year_range = range(start_year, end_year + 1)
    except ValueError:
        print("❌ --years 格式错误，应为 YYYY-YYYY")
        sys.exit(1)

    sources = [s.strip().lower() for s in args.source.split(",")]
    print(f"\n{'#' * 70}")
    print(f"#  全{DOMAIN_CONFIG.get('name', '领域')}论文批量检索")
    print(f"#  年份范围: {start_year}-{end_year}")
    print(f"#  数据源: {', '.join(sources)}")
    print(f"#  模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"{'#' * 70}")

    # Load existing papers for dedup
    print("\n📂 加载数据库已有论文...")
    existing_dois, existing_titles = load_existing_papers(args.workspace)
    print(f"  数据库已有: {len(existing_titles)} 篇 (DOI: {len(existing_dois)} 个)")

    # Search
    all_results = []
    t0 = time.time()

    if "pubmed" in sources:
        all_results.extend(search_pubmed_range(year_range, args.max_per_year))

    if "epmc" in sources:
        all_results.extend(search_europe_pmc(year_range, args.max_epmc))

    if "openalex" in sources:
        all_results.extend(search_openalex(year_range, args.max_openalex))

    if "semantic" in sources or "semantic_scholar" in sources:
        all_results.extend(search_semantic_scholar(year_range))

    if "gs" in sources or "google_scholar" in sources:
        max_gs = getattr(args, 'max_gs', 200) or 200
        all_results.extend(search_google_scholar(year_range, max_gs))

    search_elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"📊 检索完成: {len(all_results)} 篇原始结果, 用时 {search_elapsed:.1f}s")
    print(f"{'=' * 60}")

    if not all_results:
        print("⚠️ 未检索到任何论文，退出")
        return

    # Deduplicate
    candidates = deduplicate_results(all_results, existing_dois, existing_titles)

    if not candidates:
        print("⚠️ 去重后无新论文，退出")
        return

    # Export if requested
    if args.output:
        export_csv(candidates, args.output)

    # Interactive review or auto-ingest
    if args.dry_run:
        print(f"\n[DRY-RUN] 共 {len(candidates)} 篇候选论文，未写入数据库")
        return

    if not args.auto_ingest:
        print(f"\n{'=' * 60}")
        print(f"📋 候选论文: {len(candidates)} 篇")
        print(f"{'=' * 60}")
        print("\n是否全部入库? (y/n)")
        try:
            resp = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            resp = "n"
        if resp not in ("y", "yes", "是"):
            print("已取消")
            return

    # Ingest
    stats = ingest_papers(
        candidates,
        workspace=args.workspace,
        dry_run=False,
        enrich=not args.no_enrich,
    )

    print(f"\n{'=' * 60}")
    print("✅ 入库完成")
    print(f"  入库: {stats['ingested']}")
    print(f"  CrossRef 补全: {stats['enriched']}")
    print(f"  失败: {stats['failed']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
