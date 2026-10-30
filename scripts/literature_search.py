#!/usr/bin/env python3
"""
实体论文检索 + 入库脚本

三源全文检索 (PMC + Europe PMC + Google Scholar)
→ 补全文献元数据 → 待审核论文入库

用法:
  python scripts/literature_search.py --entity qmrlab --days 60
  python scripts/literature_search.py --entity qmrlab --from 2025-10-01 --to 2025-10-23
  python scripts/literature_search.py --entity qmrlab --dry-run
  python scripts/literature_search.py --entity qmrlab --years 2025-2026
"""
import logging

import argparse
import sys
import time
import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
# ── Path setup ─────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.doi import normalize_doi
from paper_library.crud import upsert_paper
from core.crossref_api import fetch_metadata_by_doi
from core.date_extractor import enrich_paper_dates
from core.llm_client import OpenAICompatibleClient
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal
from paper_library.pdf_downloader import PdfDownloader
from paper_library.author_identity import contains_han
from paper_library.time_utils import local_now
from paper_library.paper_lifecycle import complete_processing, refresh_metadata_complete
from paper_library.citation import format_apa_author_list, sanitize_apa_journal_name
from paper_library.abstract_enrichment import (
    enrich_missing_abstract,
    extract_crossref_abstract,
    needs_chinese_abstract_package,
    translate_abstract_to_chinese,
)
from core.entity_detector import get_target_entity_key, load_entities_config
from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

# ── Config ─────────────────────────────────────────────────────
NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
NCBI_API_KEY = configured_value("NCBI_API_KEY", "ncbi", "api_key") or None
NCBI_EMAIL = configured_value("NCBI_EMAIL", "ncbi", "email")
EUROPE_PMC_BASE_URL = api_endpoint("EUROPE_PMC_BASE_URL", "europe_pmc_base_url")


def _recent_date_window(days: int) -> Tuple[date, date]:
    """Return one shared search interval in the configured application timezone."""
    end_date = local_now().date()
    return end_date - timedelta(days=days), end_date


def _scholar_year_range(date_window: Tuple[date, date]) -> Tuple[int, int]:
    """Google Scholar accepts years, so use the years covering the shared interval."""
    return date_window[0].year, date_window[1].year


def _load_entity_search_config(entity_key: str) -> dict:
    """从 config/entities.yaml 加载实体检索配置。"""
    cfg = load_entities_config()
    entities = cfg.get("entities", {})
    entity = entities.get(entity_key)
    search = entity.get("search", {}) if isinstance(entity, dict) else {}
    if not search:
        available = ", ".join(
            key for key, value in entities.items()
            if isinstance(value, dict) and value.get("search")
        )
        raise SystemExit(f"Unknown or non-searchable entity '{entity_key}'. Available: {available}")
    return {"label": entity.get("label", entity_key), **search}


# 检索表达式与学术搜索词由 _init_entity_queries() 动态赋值。
ENTITY_LABEL: str = ""
QUERY_PMC: str = ""
QUERY_EPMC: str = ""
GS_QUERIES: List[str] = []


def _init_entity_queries(entity_key: str) -> dict:
    """初始化实体检索配置。"""
    global ENTITY_LABEL, QUERY_PMC, QUERY_EPMC, GS_QUERIES
    entity_cfg = _load_entity_search_config(entity_key)
    ENTITY_LABEL = entity_cfg["label"]
    QUERY_PMC = entity_cfg["pmc_query"]
    QUERY_EPMC = entity_cfg["epmc_query"]
    GS_QUERIES = entity_cfg["gs_queries"]
    return entity_cfg

RATE_LIMIT = {"pmc": 0.11 if NCBI_API_KEY else 0.34, "epmc": 0.35, "gs": 2.0, "crossref": 0.5}
TITLE_SIM_THRESHOLD = 0.85

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": contact_user_agent()})
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3,
    connect=3,
    read=2,
    backoff_factor=0.8,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
)))


def _build_pdf_downloader(config_dir: Path) -> PdfDownloader:
    """使用项目运行配置创建下载器，避免在脚本中重复保存密钥。"""
    from core.config_loader import ConfigLoader

    runtime = ConfigLoader(config_dir).load_all().get("runtime", {})
    pdf_cfg = runtime.get("pdf_download", {}) or {}
    semantic_cfg = runtime.get("semantic_scholar", {}) or {}
    return PdfDownloader(
        unpaywall_email=pdf_cfg.get("unpaywall_email"),
        semantic_scholar_api_key=semantic_cfg.get("api_key"),
        elsevier_api_key=pdf_cfg.get("elsevier_api_key"),
        ieee_api_key=pdf_cfg.get("ieee_api_key"),
        wiley_tdm_token=pdf_cfg.get("wiley_tdm_token"),
        api_endpoints=runtime.get("api_endpoints", {}) or {},
        request_interval=float(pdf_cfg.get("request_interval", 1.0)),
    )


# ── Helpers ─────────────────────────────────────────────────────
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
    # Strip trailing URL path fragments (common in GS results)
    raw = re.sub(r'/(?:full|pdf|abstract|meta|epub|html|htm)$', '', raw)
    raw = re.sub(r'[._]reference[._]pdf$', '', raw, flags=re.IGNORECASE)
    doi = normalize_doi(raw)
    return doi if doi else None


def _extract_year(pubdate: str) -> Optional[int]:
    if not pubdate: return None
    m = re.match(r"(\d{4})", pubdate.strip())
    return int(m.group(1)) if m else None


def _normalize_title(t: str) -> str:
    if not t: return ""
    t = t.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_similarity(a: str, b: str) -> float:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb: return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# ═══════════════════════════════════════════════════════════════
# Source 1: PMC (NCBI E-Utilities) full-text search
# ═══════════════════════════════════════════════════════════════
def search_pmc(years: Tuple[int, int] = None,
               max_results: int = 500,
               date_window: Tuple[date, date] | None = None) -> List[Dict]:
    print("\n" + "=" * 60)
    print("[1/3] PMC full-text search (NCBI, db=pmc)")
    print("=" * 60)

    if not NCBI_BASE_URL:
        warn_missing("PMC search", "NCBI_BASE_URL")
        return []
    url = f"{NCBI_BASE_URL}/esearch.fcgi"
    params = {"db": "pmc", "term": QUERY_PMC, "retmode": "json", "retmax": max_results}
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL

    if date_window:
        window_start, window_end = date_window
        min_d = window_start.strftime("%Y/%m/%d")
        max_d = window_end.strftime("%Y/%m/%d")
        params.update({"mindate": min_d, "maxdate": max_d, "datetype": "pdat"})
        print(f"  Date: {min_d} ~ {max_d}")
    elif years:
        params.update({
            "mindate": f"{years[0]}/01/01",
            "maxdate": f"{years[1]}/12/31",
            "datetype": "pdat",
        })
        print(f"  Date: {years[0]}-{years[1]}")

    print(f"  Query: {QUERY_PMC[:100]}")

    try:
        resp = HTTP.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        pmc_ids = data.get("esearchresult", {}).get("idlist", [])
        total = int(data.get("esearchresult", {}).get("count", 0))
        print(f"  Total hits: {total}, fetched: {len(pmc_ids)}")
    except Exception as e:
        print(f"  [FAIL] {e}")
        return []

    if not pmc_ids:
        return []

    # Fetch details via esummary
    results = []
    batch_size = 50
    for i in range(0, len(pmc_ids), batch_size):
        batch = pmc_ids[i:i + batch_size]
        time.sleep(RATE_LIMIT["pmc"])

        fetch_url = f"{NCBI_BASE_URL}/esummary.fcgi"
        fetch_params = {"db": "pmc", "id": ",".join(batch), "retmode": "json"}
        if NCBI_API_KEY:
            fetch_params["api_key"] = NCBI_API_KEY
        if NCBI_EMAIL:
            fetch_params["email"] = NCBI_EMAIL
        try:
            resp = HTTP.get(fetch_url, params=fetch_params, timeout=30)
            resp.raise_for_status()
            fdata = resp.json().get("result", {})

            for pmc_id in batch:
                article = fdata.get(pmc_id, {})
                if not article or isinstance(article, str):
                    continue
                doi = _safe_doi(
                    next((aid.get("value", "") for aid in article.get("articleids", [])
                          if aid.get("idtype") == "doi"), "")
                )
                title = article.get("title", "").strip()
                if not doi and not title:
                    continue
                results.append({
                    "source": "pmc",
                    "doi": doi,
                    "title": title,
                    "journal": article.get("source", "") or article.get("fulljournalname", ""),
                    "year": _extract_year(article.get("pubdate", "")),
                })
        except Exception as e:
            print(f"  [WARN] batch fetch failed: {e}")

    print(f"  With DOI: {sum(1 for r in results if r['doi'])}/{len(results)}")
    return results


# ═══════════════════════════════════════════════════════════════
# Source 2: Europe PMC full-text search
# ═══════════════════════════════════════════════════════════════
def search_europe_pmc(years: Tuple[int, int] = None,
                      max_results: int = 500,
                      date_window: Tuple[date, date] | None = None) -> List[Dict]:
    print("\n" + "=" * 60)
    print("[2/3] Europe PMC full-text search")
    print("=" * 60)

    query = QUERY_EPMC

    if date_window:
        window_start, window_end = date_window
        min_d = window_start.strftime("%Y-%m-%d")
        max_d = window_end.strftime("%Y-%m-%d")
        query += f" AND (FIRST_PDATE:[{min_d} TO {max_d}])"
        print(f"  Date: {min_d} ~ {max_d}")
    elif years:
        query += f" AND (FIRST_PDATE:[{years[0]}-01-01 TO {years[1]}-12-31])"
        print(f"  Date: {years[0]}-{years[1]}")

    print(f"  Query: {query[:120]}")

    if not EUROPE_PMC_BASE_URL:
        warn_missing("Europe PMC search", "EUROPE_PMC_BASE_URL")
        return []
    url = f"{EUROPE_PMC_BASE_URL}/search"
    cursor_mark = "*"
    results = []
    seen_dois = set()

    while len(results) < max_results:
        params = {
            "query": query, "format": "json",
            "pageSize": min(100, max_results - len(results)),
            "cursorMark": cursor_mark, "resultType": "core",
        }
        try:
            resp = HTTP.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("resultList", {}).get("result", [])
            if not items:
                break

            for item in items:
                doi = _safe_doi(item.get("doi", ""))
                if doi and doi not in seen_dois:
                    seen_dois.add(doi)
                    results.append({
                        "source": "europe_pmc",
                        "doi": doi,
                        "title": item.get("title", ""),
                        "journal": item.get("journalTitle", ""),
                        "year": item.get("pubYear"),
                    })

            next_cursor = data.get("nextCursorMark")
            if not next_cursor or next_cursor == cursor_mark:
                break
            cursor_mark = next_cursor
            time.sleep(RATE_LIMIT["epmc"])
        except Exception as e:
            print(f"  [FAIL] {e}")
            break

    print(f"  Total fetched: {len(results)}")
    return results


# ═══════════════════════════════════════════════════════════════
# Source 3: Google Scholar (paper-search-mcp, with as_ylo/as_yhi)
# ═══════════════════════════════════════════════════════════════
def search_google_scholar(years: Tuple[int, int] = None,
                          max_per_query: int = 20,
                          date_window: Tuple[date, date] | None = None) -> List[Dict]:
    print("\n" + "=" * 60)
    print("[3/3] Google Scholar (as_ylo/as_yhi time filter)")
    print("=" * 60)

    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        print(f"  [SKIP] Google Scholar HTML 解析依赖缺失，请安装 paper-pulse[tools]: {e}")
        return []

    try:
        from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher
    except ImportError as e:
        print(f"  [SKIP] paper-search-mcp 不可用，已跳过 Google Scholar 渠道: {e}")
        return []

    if date_window:
        years = _scholar_year_range(date_window)

    searcher = GoogleScholarSearcher(max_retries=2, retry_delay=3.0)
    all_results = []

    for qi, query in enumerate(GS_QUERIES, 1):
        print(f"  [{qi}/{len(GS_QUERIES)}] '{query}'")
        papers = []
        start = 0
        per_page = 10

        while len(papers) < max_per_query:
            params = {
                "q": query, "start": start,
                "hl": "en", "as_sdt": "0,5",
            }
            if years:
                params["as_ylo"] = years[0]
                params["as_yhi"] = years[1]

            try:
                resp = searcher.session.get(searcher.SCHOLAR_URL, params=params, timeout=30)
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                if searcher._is_captcha_page(soup):
                    print("    [WARN] CAPTCHA detected, skipping this query")
                    break

                results_divs = soup.find_all("div", class_="gs_ri")
                if not results_divs:
                    break

                for item in results_divs:
                    if len(papers) >= max_per_query:
                        break
                    paper = searcher._parse_paper(item)
                    if paper and paper.doi:
                        doi = _safe_doi(paper.doi)
                        if doi:
                            papers.append({
                                "source": "google_scholar",
                                "doi": doi,
                                "title": paper.title,
                                "year": paper.published_date.year if paper.published_date else None,
                                "abstract": paper.abstract,
                            })

                start += per_page
                time.sleep(RATE_LIMIT["gs"])
            except Exception as e:
                print(f"    [ERR] {e}")
                break

        print(f"    Found: {len(papers)}")
        all_results.extend(papers)
        time.sleep(1)

    print(f"  Total: {len(all_results)}")
    return all_results


# ═══════════════════════════════════════════════════════════════
# Dedup
# ═══════════════════════════════════════════════════════════════
def dedup_across_sources(papers: List[Dict]) -> List[Dict]:
    """Dedup by DOI first, then title similarity."""
    seen_dois: Dict[str, Dict] = {}
    no_doi: List[Dict] = []

    for p in papers:
        doi = p.get("doi")
        if doi:
            key = doi.lower()
            if key not in seen_dois:
                seen_dois[key] = p
        else:
            no_doi.append(p)

    deduped = list(seen_dois.values())

    # Title dedup for no-DOI papers against seen papers
    for p in no_doi:
        title = p.get("title", "")
        if not title:
            continue
        is_dup = False
        for existing in deduped:
            if _title_similarity(title, existing.get("title", "")) >= TITLE_SIM_THRESHOLD:
                is_dup = True
                break
        if not is_dup:
            deduped.append(p)

    print(f"\nDedup: {len(papers)} -> {len(deduped)} ({len(seen_dois)} by DOI)")
    return deduped


# ═══════════════════════════════════════════════════════════════
# Enrichment pipeline
# ═══════════════════════════════════════════════════════════════
def build_apa_from_doi_meta(doi_meta: dict) -> str:
    """Build APA 7th citation from CrossRef doi_meta.author."""
    msg = doi_meta.get("message") if "message" in doi_meta else doi_meta
    authors = msg.get("author", []) if isinstance(msg, dict) else []

    apa_authors = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        given = a.get("given", "")
        family = a.get("family", "")
        if family and not contains_han(f"{given} {family}"):
            initials = ". ".join(p[0].upper() for p in given.split()) + "." if given else ""
            apa_authors.append(f"{family}, {initials}".strip())
        else:
            name = a.get("name", "")
            if name and not contains_han(name):
                apa_authors.append(name)

    if not apa_authors:
        return ""

    ap = format_apa_author_list(apa_authors)

    year = msg.get("published-print", msg.get("published-online", {}))
    if isinstance(year, dict):
        parts = year.get("date-parts", [[None]])[0]
        year = parts[0] if parts and parts[0] else None

    title = ""
    t = msg.get("title", [])
    if isinstance(t, list) and t:
        title = t[0]
    journal = ""
    ct = msg.get("container-title", [])
    if isinstance(ct, list) and ct:
        journal = ct[0]
    volume = msg.get("volume", "")
    issue = msg.get("issue", "")
    pages = msg.get("page", "")
    doi = msg.get("DOI", "")

    yp = f"({year})." if year else ""
    tp = f"{title or '(No title)'}."
    jp = sanitize_apa_journal_name(journal)
    vp = ""
    if volume:
        vp = str(volume)
        if issue: vp += f"({issue})"
        if pages: vp += f", {pages}"
        vp += "."
    elif pages:
        vp = f"{pages}."
    dp = f" https://doi.org/{doi}" if doi else ""

    parts = [p for p in [ap, yp, tp, jp, vp + dp] if p]
    return " ".join(parts).replace("  ", " ").strip()


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════
def main():
    setup_console()
    parser = argparse.ArgumentParser(description="Entity-based paper search & ingest")
    add_db_arguments(parser)
    parser.add_argument("--entity", type=str, default=get_target_entity_key(),
                        help="实体 key（config/entities.yaml 中定义）")
    parser.add_argument("--days", type=int, default=None, help="Search last N days (default: 60)")
    parser.add_argument("--from", dest="date_from", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", type=str, default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--years", type=str, default=None, help="Year range, e.g. '2025-2026'")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB write")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess unreviewed papers (PDF + LLM)")
    parser.add_argument(
        "--translate-zh", action="store_true",
        help="Translate missing Chinese abstracts with the configured LLM model",
    )
    parser.add_argument("--workspace", default=".", help="Workspace path")
    parser.add_argument("--config-dir", default="config", help="Config directory")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    if not args.entity:
        parser.error("未设置目标实体，请使用 --entity 指定")
    _init_entity_queries(args.entity)

    if args.days is not None and not 1 <= args.days <= 1095:
        parser.error("--days must be between 1 and 1095")
    if (args.date_from is None) != (args.date_to is None):
        parser.error("--from and --to must be provided together")
    if sum((args.days is not None, args.date_from is not None, args.years is not None)) > 1:
        parser.error("--days, --from/--to, and --years are mutually exclusive")
    if args.days is None and args.date_from is None and not args.years:
        args.days = 60

    date_window = None
    if args.date_from is not None:
        try:
            date_from = date.fromisoformat(args.date_from)
            date_to = date.fromisoformat(args.date_to)
            try:
                latest_end = date_from.replace(year=date_from.year + 3)
            except ValueError:
                latest_end = date_from.replace(year=date_from.year + 3, day=28)
            if date_to < date_from:
                raise ValueError("start date is after end date")
            if date_to > latest_end:
                raise ValueError("date range exceeds three years")
            date_window = (date_from, date_to)
        except ValueError as exc:
            parser.error(f"--from/--to must be valid dates spanning no more than three years: {exc}")

    years_tuple = None
    if args.years:
        try:
            parts = args.years.split("-")
            if len(parts) != 2:
                raise ValueError
            years_tuple = (int(parts[0]), int(parts[1]))
            if years_tuple[0] > years_tuple[1]:
                raise ValueError
        except ValueError:
            parser.error("--years must be a valid ascending range, e.g. 2025-2026")

    print("=" * 60)
    print(f"Entity Paper Search: {ENTITY_LABEL} ({args.entity})")
    print("=" * 60)
    print(f"Entity: {ENTITY_LABEL}")
    if args.days:
        print(f"Time range: last {args.days} days")
    if date_window:
        print(f"Time range: {date_window[0]} ~ {date_window[1]}")
    if years_tuple:
        print(f"Time range: {years_tuple[0]}-{years_tuple[1]}")
    print(f"Dry run: {args.dry_run}")

    # ── Reprocess mode: PDF + LLM for existing unreviewed papers ──
    if args.reprocess:
        print("=" * 60)
        print("Reprocess mode: PDF download + LLM enrichment")
        print("=" * 60)

        # Import llm_enrich_paper from scripts dir
        llm_script = ROOT / "scripts" / "llm_enrich_paper.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("llm_enrich_paper", llm_script)
        llm_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(llm_module)
        enrich_paper = llm_module.enrich_paper

        from core.config_loader import ConfigLoader as CL

        config_loader = CL(ROOT / args.config_dir)
        config = config_loader.load_all()
        rt = config.get("runtime", {})
        llm_cfg = rt.get("provider", {})
        llm_client = OpenAICompatibleClient(
            base_url=llm_cfg.get("base_url", ""),
            api_key=llm_cfg.get("api_key", ""),
            timeout_seconds=120,
        )

        pdf_dl = None
        try:
            pdf_dl = _build_pdf_downloader((ROOT / args.config_dir).resolve())
        except Exception as e:
            print(f"[WARN] PDF downloader not available: {e}")

        with get_session(workspace=args.workspace, config_dir=args.config_dir) as session:
            papers = session.query(Paper).filter(
                Paper.review_outcome == "unreviewed"
            ).all()
            print(f"Found {len(papers)} unreviewed papers\n")

            for i, paper in enumerate(papers, 1):
                print(f"[{i}/{len(papers)}] id={paper.id} {paper.title[:60] if paper.title else 'N/A'}")

                # PDF download
                if pdf_dl and not paper.pdf_storage_path:
                    if args.dry_run:
                        print("  PDF: dry-run")
                    else:
                        print("  PDF...", end=" ")
                        try:
                            dl_result = pdf_dl.download_for_paper(
                                paper, session, workspace=str(Path(args.workspace).resolve())
                            )
                            if dl_result.success:
                                print(f"OK ({dl_result.method})")
                            else:
                                print(f"skip ({dl_result.error[:60] if dl_result.error else 'no'})")
                        except Exception as e:
                            print(f"skip ({e})")
                else:
                    print(f"  PDF: {'already has' if paper.pdf_storage_path else 'no downloader'}")

                # LLM enrichment (abstract)
                print("  LLM...", end=" ")
                if args.dry_run:
                    print("dry-run")
                else:
                    try:
                        enrich_paper(paper.id, session, llm_client, source="abstract")
                        session.commit()
                    except Exception as e:
                        print(f"FAIL ({e})")

            print(f"\nDone. Reprocessed {len(papers)} papers.")
        return

    # ── Search ──
    all_results = []
    search_window = _recent_date_window(args.days) if args.days else date_window
    all_results.extend(search_pmc(
        years=years_tuple, date_window=search_window
    ))
    all_results.extend(search_europe_pmc(
        years=years_tuple, date_window=search_window
    ))
    # Google Scholar only supports year-level filtering; PMC and Europe PMC use exact dates.
    gs_years = _scholar_year_range(search_window) if search_window else years_tuple
    all_results.extend(search_google_scholar(years=gs_years, date_window=search_window))

    print(f"\nTotal raw results: {len(all_results)}")

    # ── Dedup across sources ──
    deduped = dedup_across_sources(all_results)
    # Filter: must have DOI
    deduped = [p for p in deduped if p.get("doi")]
    print(f"With DOI: {len(deduped)}")

    # ── Dedup against DB ──
    with get_session(workspace=args.workspace, config_dir=args.config_dir) as session:
        existing_dois = set()
        all_dois = session.query(Paper.doi).filter(Paper.doi.isnot(None)).all()
        for (d,) in all_dois:
            if d:
                existing_dois.add(d.lower().strip())

        new_papers = []
        existing_papers = []
        for p in deduped:
            doi = p["doi"].lower().strip()
            if doi in existing_dois:
                existing_papers.append(p)
            else:
                new_papers.append(p)

        print(f"New: {len(new_papers)}, Already in DB: {len(existing_papers)}")

        if args.dry_run:
            print("\n[Dry-run] Would import:")
            for p in new_papers[:10]:
                print(f"  {p['doi']} | {p['title'][:80]}")
            if len(new_papers) > 10:
                print(f"  ... and {len(new_papers) - 10} more")
            print(f"\n{'=' * 60}")
            print("Report")
            print(f"{'=' * 60}")
            print(f"  Raw results:     {len(all_results)}")
            print(f"  After dedup:     {len(deduped)}")
            print(f"  New papers:      {len(new_papers)}")
            print(f"  Existing (DB):   {len(existing_papers)}")
            print(f"  Would import:    {len(new_papers)}")
            return

        # The same enrichment dependencies are shared by existing and new
        # search hits so both paths receive consistent post-ingest treatment.
        jcr_matcher = None
        try:
            jcr_matcher = JcrMatcher().load()
        except Exception as e:
            print(f"[WARN] JCR matcher not available: {e}")

        pdf_downloader = None
        try:
            pdf_downloader = _build_pdf_downloader((ROOT / args.config_dir).resolve())
        except Exception as e:
            print(f"[WARN] PDF downloader not available: {e}")

        translation_client = None
        translation_model = ""
        try:
            from core.config_loader import ConfigLoader
            loaded = ConfigLoader((ROOT / args.config_dir).resolve()).load_all()
            abstract_cfg = loaded.get("runtime", {}).get("abstract_enrichment", {}) or {}
            translation_enabled = args.translate_zh or (
                abstract_cfg.get("enabled", False) and abstract_cfg.get("translate_zh", False)
            )
            if translation_enabled:
                provider = loaded.get("runtime", {}).get("provider", {}) or {}
                sequence = loaded.get("models", {}).get("routing", {}).get("default_auto_sequence", [])
                translation_model = abstract_cfg.get("model") or provider.get("model") or (sequence[0] if sequence else "")
                candidate = OpenAICompatibleClient(
                    base_url=provider.get("base_url", ""),
                    api_key=provider.get("api_key", ""),
                    timeout_seconds=300,
                    max_retries=3,
                )
                if candidate.available() and translation_model:
                    translation_client = candidate
        except Exception as e:
            print(f"[WARN] Abstract translation disabled: {e}")

        # ── Re-enrich papers already present in the DB ──
        patched = 0
        existing_enriched = 0
        existing_errors = 0
        existing_translated = 0
        for i, source_paper in enumerate(existing_papers, 1):
            doi = source_paper["doi"].lower().strip()
            paper = session.query(Paper).filter(Paper.doi.ilike(doi)).first()
            if not paper:
                continue
            print(f"\n[existing {i}/{len(existing_papers)}] {doi[:50]}")
            try:
                if not paper.entity_label:
                    paper.entity_label = ENTITY_LABEL
                    paper.entity_source = "literature_search"
                    patched += 1

                raw = paper.doi_meta if isinstance(paper.doi_meta, dict) else {}
                needs_crossref = not raw or any(
                    not getattr(paper, field, None)
                    for field in ("abstract", "journal_name", "year")
                )
                if needs_crossref and paper.doi:
                    time.sleep(RATE_LIMIT["crossref"])
                    meta = fetch_metadata_by_doi(paper.doi, structured=True)
                    if meta and not meta.get("_error"):
                        raw = meta.get("raw") or raw
                        paper.doi_meta = raw or paper.doi_meta
                        paper.abstract = paper.abstract or extract_crossref_abstract(meta)
                        paper.journal_name = paper.journal_name or (
                            (raw.get("container-title") or [None])[0]
                        )
                        paper.year = paper.year or meta.get("year")
                        paper.volume = paper.volume or (str(raw.get("volume")) if raw.get("volume") else None)
                        paper.issue = paper.issue or (str(raw.get("issue")) if raw.get("issue") else None)
                        paper.pages = paper.pages or (str(raw.get("page")) if raw.get("page") else None)
                        paper.apa_citation = paper.apa_citation or build_apa_from_doi_meta(raw)

                if raw:
                    normalize_authors_for_paper(raw, paper.id, session)
                if jcr_matcher:
                    enrich_paper_journal(paper, session, jcr_matcher)

                if pdf_downloader and paper.doi and not paper.pdf_storage_path:
                    dl_result = pdf_downloader.download_for_paper(
                        paper, session, workspace=str(Path(args.workspace).resolve())
                    )
                    print(f"  PDF: {dl_result.method if dl_result.success else 'missing'}")

                enrich_paper_dates(paper)
                abstract_result = enrich_missing_abstract(
                    paper, refresh_crossref=not bool(raw),
                )
                print(f"  Abstract: {abstract_result.source}")
                if translation_client and paper.abstract and needs_chinese_abstract_package(paper):
                    translated = translate_abstract_to_chinese(
                        paper, translation_client, model=translation_model,
                    )
                    if translated.changed:
                        existing_translated += 1
                    print(f"  Chinese: {translated.source}")

                if paper.processing_status in ("pending", "failed"):
                    complete_processing(paper)
                refresh_metadata_complete(paper)
                session.commit()
                existing_enriched += 1
            except Exception as e:
                session.rollback()
                existing_errors += 1
                print(f"  [WARN] existing enrichment failed: {e}")

        imported = 0
        excluded = 0

        for i, p in enumerate(new_papers, 1):
            doi = p["doi"]
            title = p.get("title", "")[:120]
            print(f"\n[{i}/{len(new_papers)}] {doi[:50]}")

            try:
                # ① CrossRef enrichment
                print("  [1/7] CrossRef...", end=" ")
                time.sleep(RATE_LIMIT["crossref"])
                meta = fetch_metadata_by_doi(doi, structured=True)
                if not meta or meta.get("_error"):
                    print(f"excluded: {meta.get('_error', 'no result') if meta else 'no result'}")
                    excluded += 1
                    continue
                raw = meta.get("raw") or {}
                print("OK")

                # ② JCR journal
                print("  [2/7] JCR...", end=" ")
                if jcr_matcher:
                    try:
                        # Will be done after paper creation
                        print("will match after insert")
                    except Exception:
                        print("skip")
                else:
                    print("skip (no matcher)")

                # ③ APA citation
                print("  [3/7] APA...", end=" ")
                apa = build_apa_from_doi_meta(raw) if raw else ""
                print(f"OK ({len(apa)} chars)" if apa else "skip")

                # Build paper_data
                paper_data = {
                    "doi": doi,
                    "title": meta.get("title") or title or "(无标题)",
                    "year": meta.get("year"),
                    "abstract": extract_crossref_abstract(meta),
                    "journal_name": raw.get("container-title", [None])[0] if raw.get("container-title") else None,
                    "volume": str(raw.get("volume")) if raw.get("volume") else None,
                    "issue": str(raw.get("issue")) if raw.get("issue") else None,
                    "pages": str(raw.get("page")) if raw.get("page") else None,
                    "doi_meta": raw,
                    "apa_citation": apa or None,
                    "entity_label": ENTITY_LABEL,
                    "entity_source": "literature_search",
                    "source": f"{args.entity}_literature_search",
                    "processing_status": "done",
                    "review_outcome": "unreviewed",
                }

                # ④ + upsert: 作者标准化在 upsert_paper 之后
                paper = upsert_paper(session, paper_data)
                session.flush()

                # ④ Author normalization
                print("  [4/7] Authors...", end=" ")
                if raw:
                    n = normalize_authors_for_paper(raw, paper.id, session)
                    print(f"OK ({n} authors)")
                else:
                    print("skip")

                # ② JCR match (after paper exists)
                if jcr_matcher:
                    try:
                        enrich_paper_journal(paper, session, jcr_matcher)
                    except Exception:
                        logger.warning('处理失败，已跳过该项（main）', exc_info=True)
                        pass

                # ⑤ Date extraction
                print("  [5/7] Dates...", end=" ")
                try:
                    enrich_paper_dates(paper)
                    print("OK")
                except Exception as e:
                    print(f"skip ({e})")

                # ⑥ PDF download (best-effort, includes archive + extract)
                print("  [6/7] PDF...", end=" ")
                if pdf_downloader and paper.doi:
                    try:
                        dl_result = pdf_downloader.download_for_paper(
                            paper, session, workspace=str(Path(args.workspace).resolve())
                        )
                        if dl_result.success:
                            print(f"OK ({dl_result.method})")
                        else:
                            print(f"skip ({dl_result.error[:60] if dl_result.error else 'no'})")
                    except Exception as e:
                        print(f"skip ({e})")
                else:
                    print("skip")

                # ⑦ Abstract fallback + optional Chinese translation.  The
                # live Crossref request above was already the retry for this DOI.
                abstract_result = enrich_missing_abstract(paper, refresh_crossref=False)
                print(f"  [7/7] Abstract: {abstract_result.source}")
                if translation_client and paper.abstract and needs_chinese_abstract_package(paper):
                    translated = translate_abstract_to_chinese(
                        paper, translation_client, model=translation_model,
                    )
                    print(f"        Chinese: {translated.source}")

                if paper.processing_status != "failed":
                    complete_processing(paper)
                else:
                    refresh_metadata_complete(paper)
                session.commit()
                imported += 1
                print(f"  -> unreviewed (id={paper.id})")

            except Exception as e:
                print(f"  [FAIL] {e}")
                session.rollback()
                excluded += 1

        print(f"\n{'=' * 60}")
        print("Report")
        print(f"{'=' * 60}")
        print(f"  Raw results:     {len(all_results)}")
        print(f"  After dedup:     {len(deduped)}")
        print(f"  New papers:      {len(new_papers)}")
        print(f"  Existing (DB):   {len(existing_papers)}")
        print(f"  Imported:        {imported}")
        print(f"  Excluded:        {excluded}")
        print(f"  Entity patched:  {patched}")
        print(f"  Existing enriched: {existing_enriched}")
        print(f"  Existing errors:   {existing_errors}")
        print(f"  Chinese translated: {existing_translated}")


if __name__ == "__main__":
    main()
