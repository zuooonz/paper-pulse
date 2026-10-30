"""基本 CRUD 操作"""
import logging

import time
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.orm import Session

from paper_library.models import Paper, Journal
from paper_library.author_identity import contains_han
from paper_library.abstract_enrichment import extract_crossref_abstract
from core.crossref_api import clean_crossref_abstract
from paper_library.citation import (
    format_apa_author_list,
    sanitize_apa_journal_name,
    sanitize_scholarly_markup,
)
from paper_library.paper_lifecycle import (
    refresh_metadata_complete,
)
from core.domain_config import generation_requires_target_entity
from core.entity_detector import get_target_label

logger = logging.getLogger(__name__)

def _ensure_doi_meta(doi: str, paper_data: dict, existing: Optional[Paper]) -> None:
    """从 CrossRef 补全 doi_meta 缺失字段，生成 first_authors + APA 引用。"""
    existing_meta = None
    if existing and existing.doi_meta:
        existing_meta = existing.doi_meta
    raw = paper_data.get("doi_meta") or existing_meta

    # 1. doi_meta 不完整，或论文仍缺摘要且缓存元数据也没有摘要 → 实时重拉
    needs_abstract_retry = not paper_data.get("abstract") and not extract_crossref_abstract(raw)
    if not raw or not isinstance(raw, dict) or len(raw) <= 5 or needs_abstract_retry:
        try:
            from core.crossref_api import fetch_metadata_by_doi
            time.sleep(0.1)
            result = fetch_metadata_by_doi(doi, structured=True, timeout_sec=6)
            if result and not result.get("_error") and not result.get("error"):
                fresh = result.get("raw") or {}
                if fresh and isinstance(fresh, dict) and len(fresh) > 5:
                    raw = fresh
                    paper_data["doi_meta"] = raw
        except Exception:
            logger.warning('处理失败，已跳过该项（_ensure_doi_meta）', exc_info=True)
            pass

    if not raw or not isinstance(raw, dict):
        return

    # 2. 缺字段补全（从 raw 提取，不再依赖调用方）
    if not paper_data.get("abstract"):
        abstract = extract_crossref_abstract(raw)
        if abstract:
            paper_data["abstract"] = abstract
    if not paper_data.get("volume"):
        v = raw.get("volume")
        if v:
            paper_data["volume"] = str(v)
    if not paper_data.get("issue"):
        i = raw.get("issue")
        if i:
            paper_data["issue"] = str(i)
    if not paper_data.get("pages"):
        p = raw.get("page")
        if p:
            paper_data["pages"] = str(p)
    if not paper_data.get("journal_name"):
        ct = raw.get("container-title")
        if ct and isinstance(ct, list) and ct[0]:
            paper_data["journal_name"] = ct[0]
    if not paper_data.get("year"):
        pub = raw.get("published-print", raw.get("published-online", {}))
        parts = pub.get("date-parts", [[None]])[0] if isinstance(pub, dict) else [None]
        if parts and parts[0]:
            paper_data["year"] = int(parts[0])

    # 3. 生成 APA 引用（作者从 CrossRef 元数据中读取）
    if not paper_data.get("apa_citation"):
        _build_apa_for_paper_data(paper_data, raw)

    # 5. 标记与当前目标实体关联的论文。
    from core.entity_detector import get_target_label
    target_label = get_target_label()
    if target_label and paper_data.get("entity_label") == target_label:
        paper_data["cites_target"] = True


def _build_apa_for_paper_data(pd: dict, raw: dict) -> None:
    """生成 APA 7th 引用存入 paper_data['apa_citation'].

    Uses CrossRef author list (English names) for proper APA formatting.
    Falls back to first_authors/excel data if CrossRef data is unavailable.
    """
    # Prefer CrossRef authors for English names (APA format needs surname, initials)
    xref_authors = raw.get("author", [])
    if xref_authors:
        author_names = []
        for a in xref_authors:
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip() if given else family
            if not name:
                name = a.get("name", "")
            if name and not contains_han(name):
                author_names.append(name)
    else:
        author_names = []
    apa_authors = []
    for n in author_names:
        parts = n.strip().split()
        if len(parts) == 1:
            apa_authors.append(parts[0])
        else:
            surname = parts[-1]
            initials = ". ".join(p[0].upper() for p in parts[:-1]) + "."
            apa_authors.append(f"{surname}, {initials}")

    ap = format_apa_author_list(apa_authors)

    year = pd.get("year")
    title = sanitize_scholarly_markup(pd.get("title", ""))
    journal = sanitize_apa_journal_name(pd.get("journal_name", ""))
    volume = pd.get("volume", "")
    issue = pd.get("issue", "")
    pages = pd.get("pages", "")
    doi = pd.get("doi", "")

    yp = f"({year})." if year else ""
    tp = f"{title or '(No title)'}."
    jp = journal or ""
    vp = ""
    if volume:
        vp = str(volume)
        if issue:
            vp += f"({issue})"
        if pages:
            vp += f", {pages}"
        vp += "."
    elif pages:
        vp = f"{pages}."
    dp = f" https://doi.org/{doi}" if doi else ""
    parts = [p for p in [ap, yp, tp, jp, vp + dp] if p]
    pd["apa_citation"] = " ".join(parts).replace("  ", " ").strip()


def upsert_paper(session: Session, paper_data: dict) -> Paper:
    """按 DOI 去重插入/更新论文。DOI 为空时按标题查重。

    有 DOI 时自动从 CrossRef 补全缺失的元数据（doi_meta / abstract / volume 等）。
    """
    if paper_data.get("title") is not None:
        paper_data["title"] = sanitize_scholarly_markup(paper_data["title"])
    if paper_data.get("apa_citation") is not None:
        paper_data["apa_citation"] = sanitize_scholarly_markup(paper_data["apa_citation"])
    if paper_data.get("abstract") is not None:
        paper_data["abstract"] = clean_crossref_abstract(paper_data["abstract"]) or None

    doi = paper_data.get("doi")
    if doi is not None and str(doi).strip() == "":
        doi = None
        paper_data["doi"] = None
    title = paper_data.get("title")

    existing = None
    if doi:
        existing = get_paper_by_doi(session, doi)
    elif title:
        stmt = select(Paper).where(Paper.title.ilike(title.strip()))
        existing = session.scalar(stmt)

    # 自动补全 CrossRef 元数据
    if doi:
        _ensure_doi_meta(doi, paper_data, existing)

    # Crossref enrichment may have generated a citation during this call.
    if paper_data.get("title") is not None:
        paper_data["title"] = sanitize_scholarly_markup(paper_data["title"])
    if paper_data.get("apa_citation") is not None:
        paper_data["apa_citation"] = sanitize_scholarly_markup(paper_data["apa_citation"])

    if existing:
        # 已存在论文只更新元数据，不覆盖状态、实体和内部编号
        _PRESERVED_FIELDS = {
            "processing_status", "processing_error", "review_outcome", "reviewed_by",
            "reviewed_at", "review_note", "has_pdf", "metadata_complete",
            "entity_label", "entity_source", "entity_confidence", "internal_id", "source",
        }
        for key, value in paper_data.items():
            if key != "id" and key not in _PRESERVED_FIELDS and hasattr(existing, key):
                setattr(existing, key, value)
        incoming_entity_label = paper_data.get("entity_label")
        automatic_entity_sources = {
            "doi_metadata", "doi_import", "fulltext_keyword", "crossref", "literature_search",
        }
        if incoming_entity_label and (
            not existing.entity_label or existing.entity_source in automatic_entity_sources
        ):
            existing.entity_label = incoming_entity_label
            if "entity_source" in paper_data:
                existing.entity_source = paper_data["entity_source"]
            if "entity_confidence" in paper_data:
                existing.entity_confidence = paper_data["entity_confidence"]
        session.add(existing)
        session.flush()
        session.expire(existing, ["authors"])
        refresh_metadata_complete(existing)
        return existing
    else:
        paper = Paper(**paper_data)
        session.add(paper)
        session.flush()
        refresh_metadata_complete(paper)
        return paper


def get_paper_by_id(session: Session, paper_id: int) -> Optional[Paper]:
    """根据 ID 获取论文"""
    return session.get(Paper, paper_id)


def get_paper_by_doi(session: Session, doi: str) -> Optional[Paper]:
    """根据 DOI 获取论文"""
    stmt = select(Paper).where(Paper.doi == doi.strip())
    return session.scalar(stmt)


def get_papers_ready(session: Session, limit: int = 100) -> List[Paper]:
    """返回已处理、已审核且符合生成实体范围的论文。

    这些论文已经完成数据采集，可以生成推文
    """
    target_label = get_target_label()
    require_target = generation_requires_target_entity()
    if require_target and not target_label:
        return []
    stmt = (
        select(Paper)
        .where(Paper.processing_status == "done")
        .where(Paper.review_outcome == "approved")
        .where(Paper.article_status == "not_generated")
        .order_by(Paper.created_at.asc())
    )
    if require_target:
        stmt = stmt.where(Paper.entity_label == target_label)
    stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def upsert_journal(session: Session, journal_data: dict) -> Journal:
    """插入/更新期刊，按 ISSN/EISSN/名称去重"""
    issn = journal_data.get("issn")
    eissn = journal_data.get("eissn")
    name = journal_data.get("name")
    existing = None

    # 1. 按 ISSN 查找（排除无效值如 N/A）
    if issn and issn.strip().upper() not in ("N/A", "NA", "", "NONE"):
        stmt = select(Journal).where(Journal.issn == issn.strip())
        existing = session.scalar(stmt)

    # 2. 按 EISSN 查找
    if not existing and eissn and eissn.strip().upper() not in ("N/A", "NA", "", "NONE"):
        stmt = select(Journal).where(Journal.eissn == eissn.strip())
        existing = session.scalar(stmt)

    # 3. 按名称精确查找
    if not existing and name:
        stmt = select(Journal).where(Journal.name == name.strip())
        existing = session.scalar(stmt)

    if existing:
        # 更新时保留已有的有效 ISSN/EISSN
        for key, value in journal_data.items():
            if key == "id":
                continue
            if key in ("issn", "eissn") and value:
                # 如果新值无效，保留旧值
                if str(value).strip().upper() in ("N/A", "NA", "", "NONE"):
                    continue
            if hasattr(existing, key):
                setattr(existing, key, value)
        session.add(existing)
        session.flush()
        return existing
    else:
        journal = Journal(**journal_data)
        session.add(journal)
        session.flush()
        return journal
