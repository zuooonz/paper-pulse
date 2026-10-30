"""Shared paper query and serialization helpers."""

from typing import Any
from paper_library.models import Author
from sqlalchemy import String as DbString
from fastapi import HTTPException
from paper_library.models import JournalMetric
from typing import List
from typing import Optional
from paper_library.models import Paper
from paper_library.models import PaperAuthorMention
from pathlib import Path
from sqlalchemy import and_
from sqlalchemy import asc
from sqlalchemy import case
from paper_library.author_identity import contains_han
from collections import defaultdict
from sqlalchemy import desc
from core.taxonomy_config import dimension_aliases
from paper_library.time_utils import format_api_datetime
from sqlalchemy import func
from paper_library.sql_dialect import json_array_contains
from paper_library.sql_dialect import json_array_of_objects_field_equals
from paper_library.sql_dialect import json_field_equals
from core.taxonomy_config import load_taxonomy_config

MAX_FILTERED_PAPER_IDS = 200


def can_view_paper(paper: Paper, user: dict) -> bool:
    """Apply the same visibility rules as the detail endpoint."""
    role = user.get("role")
    outcome = getattr(paper, "review_outcome", None)
    if outcome == "excluded" and role not in {"admin", "reviewer"}:
        return False
    if role == "limited" and outcome != "approved":
        return False
    return True


def ensure_paper_visible(paper: Paper | None, user: dict) -> Paper:
    if paper is None or not can_view_paper(paper, user):
        raise HTTPException(status_code=404, detail="Paper not found")
    return paper
import logging
from paper_library.corresponding_affiliation import normalize_department
from paper_library.tag_enrichment import normalize_multimodal_techniques
from paper_library.tag_enrichment import normalize_paradigms
from sqlalchemy import or_
from paper_library.models import paper_authors as pa_tbl
import re
from paper_library.citation import sanitize_scholarly_markup
from ...config import settings
from tools import storage
from paper_library.journal_enrichment import JcrMatcher
import sys
from sqlalchemy import text
from paper_library.paper_lifecycle import can_review_paper, is_usable_for_generation
from core.entity_detector import get_target_label
from core.domain_config import generation_requires_target_entity, load_domain
import threading
import time

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

logger = logging.getLogger(__name__)

_jcr_matcher: Optional[JcrMatcher] = None


def _get_jcr_matcher() -> Optional[JcrMatcher]:
    global _jcr_matcher
    if _jcr_matcher is None:
        _jcr_matcher = JcrMatcher(workspace=settings.workspace)
        _jcr_matcher.load()
    return _jcr_matcher

PAPER_FILTER_PROCESSING_STATUSES = {"pending", "processing", "done", "failed"}
PAPER_FILTER_REVIEW_OUTCOMES = {"unreviewed", "approved", "excluded"}

def _current_taxonomy() -> dict[str, Any]:
    return load_taxonomy_config(Path(settings.workspace) / settings.config_dir)

def _taxonomy_values(taxonomy: dict[str, Any], dimension: str, value: str) -> list[str]:
    for label, aliases in dimension_aliases(taxonomy, dimension).items():
        if label == value or any(alias.casefold() == value.casefold() for alias in aliases):
            return list(aliases)
    return [value]

def _taxonomy_filter_values(dimension: str, value: str) -> list[str]:
    return _taxonomy_values(_current_taxonomy(), dimension, value)

_PAPER_AUTHOR_ROLE_FLAGS = frozenset({"is_first", "is_corresponding"})

COUNTRY_ZH_TO_EN = {
    "中国": "China", "美国": "United States", "日本": "Japan",
    "韩国": "South Korea", "德国": "Germany", "英国": "United Kingdom",
    "加拿大": "Canada", "澳大利亚": "Australia", "法国": "France",
    "意大利": "Italy", "西班牙": "Spain", "荷兰": "Netherlands",
    "瑞士": "Switzerland", "瑞典": "Sweden", "新加坡": "Singapore",
    "印度": "India", "巴西": "Brazil", "土耳其": "Turkey",
    "伊朗": "Iran", "以色列": "Israel", "比利时": "Belgium",
    "丹麦": "Denmark", "芬兰": "Finland", "挪威": "Norway",
    "波兰": "Poland", "俄罗斯": "Russia", "奥地利": "Austria",
    "葡萄牙": "Portugal", "墨西哥": "Mexico",
}

COUNTRY_EN_TO_ZH = {value: key for key, value in COUNTRY_ZH_TO_EN.items()}

_FILTER_OPTIONS_CACHE_TTL_SECONDS = 300

_filter_options_cache: dict[tuple, tuple[float, dict]] = {}

_filter_options_cache_lock = threading.Lock()

_PAPER_LIST_CACHE_TTL_SECONDS = 60

_paper_list_cache: dict[tuple, tuple[float, dict]] = {}

_paper_list_cache_lock = threading.Lock()

def _database_change_token(session) -> tuple[int, int]:
    """Cheaply invalidate facet cache after any SQLite database write."""
    try:
        database = session.get_bind().url.database
        stat = Path(database).stat()
        return stat.st_mtime_ns, stat.st_size
    except (AttributeError, OSError, TypeError):
        return 0, 0

def _get_cached_filter_options(key: tuple) -> dict | None:
    now = time.monotonic()
    with _filter_options_cache_lock:
        cached = _filter_options_cache.get(key)
        if cached is None or now - cached[0] > _FILTER_OPTIONS_CACHE_TTL_SECONDS:
            if cached is not None:
                _filter_options_cache.pop(key, None)
            return None
        return cached[1]

def _set_cached_filter_options(key: tuple, value: dict) -> None:
    now = time.monotonic()
    with _filter_options_cache_lock:
        # Bound memory even when users explore many unique filter combinations.
        if len(_filter_options_cache) >= 128:
            oldest = min(_filter_options_cache, key=lambda item: _filter_options_cache[item][0])
            _filter_options_cache.pop(oldest, None)
        _filter_options_cache[key] = (now, value)

def _get_cached_paper_list(key: tuple) -> dict | None:
    now = time.monotonic()
    with _paper_list_cache_lock:
        cached = _paper_list_cache.get(key)
        if cached is None or now - cached[0] > _PAPER_LIST_CACHE_TTL_SECONDS:
            if cached is not None:
                _paper_list_cache.pop(key, None)
            return None
        return cached[1]

def _set_cached_paper_list(key: tuple, value: dict) -> None:
    now = time.monotonic()
    with _paper_list_cache_lock:
        if len(_paper_list_cache) >= 128:
            oldest = min(_paper_list_cache, key=lambda item: _paper_list_cache[item][0])
            _paper_list_cache.pop(oldest, None)
        _paper_list_cache[key] = (now, value)


def _extract_authors_from_doi_meta(doi_meta: dict | None) -> list[dict] | None:
    """从 CrossRef doi_meta JSON 中提取作者列表。"""
    if not doi_meta or not isinstance(doi_meta, dict):
        return None
    # doi_meta 可能是完整响应（含 message）或已解包的 message
    msg = doi_meta.get("message") if "message" in doi_meta else doi_meta
    if not msg or not isinstance(msg, dict):
        return None
    authors = msg.get("author") or msg.get("authors")
    if not authors or not isinstance(authors, list):
        return None
    result = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        # CrossRef 标准格式：given + family
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip() if given else family
        # 有些机构作者只有 name 字段
        if not name:
            name = a.get("name", "")
        if not name:
            continue
        # 提取 affiliation
        aff_list = a.get("affiliation", [])
        affiliation = ""
        if isinstance(aff_list, list) and aff_list:
            aff_name = aff_list[0].get("name", "") if isinstance(aff_list[0], dict) else str(aff_list[0])
            affiliation = aff_name
        result.append({"name": name, "affiliation": affiliation})
    return result if result else None

def _extract_authors_from_original_data(original_data: dict | None) -> list[dict] | None:
    """从导入时保留的原始记录中提取作者，保持原始字符串顺序。"""
    if not original_data or not isinstance(original_data, dict):
        return None
    raw_authors = next(
        (original_data.get(key) for key in ("authors", "Authors", "作者") if original_data.get(key)),
        None,
    )
    if isinstance(raw_authors, str):
        names = [name.strip() for name in re.split(r"\s*;\s*", raw_authors) if name.strip()]
        return [{"name": name, "affiliation": ""} for name in names] or None
    if not isinstance(raw_authors, list):
        return None
    result = []
    for author in raw_authors:
        if isinstance(author, str) and author.strip():
            result.append({"name": author.strip(), "affiliation": ""})
        elif isinstance(author, dict):
            name = str(author.get("name") or author.get("full_name") or "").strip()
            if name:
                result.append({"name": name, "affiliation": str(author.get("affiliation") or "")})
    return result or None

def _get_ordered_authors_for_papers(session, paper_ids: list[int]) -> dict[int, list[dict]]:
    """Return paper-specific author snapshots with strict language fields.

    A paper mention is the authoritative display record.  Legacy person fields
    are used only when no mention exists and only when their script matches the
    requested field.  In particular, a Han-only relation name must never leak
    into the publication's English-name field.
    """
    from sqlalchemy import select
    if not paper_ids:
        return {}

    stmt = (
        select(
            pa_tbl.c.paper_id,
            Author.id,
            Author.name.label("person_name"),
            Author.name_zh.label("person_name_zh"),
            Author.affiliation.label("person_affiliation"),
            Author.affiliation_zh.label("person_affiliation_zh"),
            PaperAuthorMention.id.label("mention_id"),
            PaperAuthorMention.name_raw,
            PaperAuthorMention.name_zh.label("mention_name_zh"),
            PaperAuthorMention.affiliation_raw,
            PaperAuthorMention.affiliation_zh.label("mention_affiliation_zh"),
            case(
                (PaperAuthorMention.id.is_not(None), PaperAuthorMention.email_raw),
                else_=Author.email,
            ).label("email"),
            case(
                (PaperAuthorMention.id.is_not(None), PaperAuthorMention.orcid_raw),
                else_=Author.orcid,
            ).label("orcid"),
            PaperAuthorMention.source_type.label("author_source"),
            PaperAuthorMention.verification_status.label("author_verification_status"),
            pa_tbl.c.author_order,
            pa_tbl.c.is_first,
            pa_tbl.c.is_corresponding,
        )
        .join(Author, pa_tbl.c.author_id == Author.id)
        .outerjoin(
            PaperAuthorMention,
            (PaperAuthorMention.paper_id == pa_tbl.c.paper_id)
            & (PaperAuthorMention.author_id == pa_tbl.c.author_id),
        )
        .where(pa_tbl.c.paper_id.in_(paper_ids))
        .order_by(pa_tbl.c.paper_id, pa_tbl.c.author_order)
    )
    result: dict[int, list[dict]] = defaultdict(list)
    for row in session.execute(stmt):
        if row.mention_id is not None:
            name_en = (row.name_raw or "") if not contains_han(row.name_raw) else ""
            name_zh = row.mention_name_zh if contains_han(row.mention_name_zh) else ""
            affiliation_en = (
                (row.affiliation_raw or "") if not contains_han(row.affiliation_raw) else ""
            )
            affiliation_zh = (
                row.mention_affiliation_zh
                if contains_han(row.mention_affiliation_zh) else ""
            )
        else:
            name_en = (row.person_name or "") if not contains_han(row.person_name) else ""
            name_zh = row.person_name_zh if contains_han(row.person_name_zh) else ""
            affiliation_en = (
                (row.person_affiliation or "")
                if not contains_han(row.person_affiliation) else ""
            )
            affiliation_zh = (
                row.person_affiliation_zh
                if contains_han(row.person_affiliation_zh) else ""
            )
        result[row.paper_id].append({
            "author_id": row.id,
            "name": name_en,
            "name_en": name_en,
            "name_zh": name_zh,
            "affiliation": affiliation_zh or affiliation_en,
            "affiliation_en": affiliation_en,
            "affiliation_zh": affiliation_zh,
            "is_first": row.is_first,
            "is_corresponding": row.is_corresponding,
            "author_order": row.author_order,
            "email": row.email,
            "orcid": row.orcid,
            "author_source": row.author_source,
            "author_verification_status": row.author_verification_status,
        })
    return dict(result)

def _get_ordered_authors(session, paper_id: int) -> list[dict]:
    """Return one paper's ordered author snapshots."""
    return _get_ordered_authors_for_papers(session, [paper_id]).get(paper_id, [])

def _paper_to_dict(
    p: Paper, impact_factor: Optional[float] = None, jcr_quartile: Optional[str] = None,
    cas_quartile: Optional[str] = None, emerging_quartile: Optional[str] = None,
    detailed: bool = False, session=None,
    ordered_authors: Optional[list[dict]] = None,
) -> dict:
    # authors / first_authors / corresponding_authors 全部从 paper_authors 表构建
    if ordered_authors is None and session:
        ordered_authors = _get_ordered_authors(session, p.id)
    ordered_authors = ordered_authors or []

    if ordered_authors:
        authors = ordered_authors
        first_authors = [a for a in ordered_authors if a.get("is_first")]
        corresponding_authors = [a for a in ordered_authors if a.get("is_corresponding")]
    else:
        authors = first_authors = corresponding_authors = []

    # 完整署名列表与角色编辑列表分离。优先使用不会被去重或人工角色调整
    # 改写顺序的原始数据；关系表仅作为缺少原始元数据时的回退。
    full_authors = _extract_authors_from_doi_meta(p.doi_meta)
    full_authors_source = "crossref"
    if not full_authors:
        full_authors = _extract_authors_from_original_data(p.original_data)
        full_authors_source = "original_data"
    if not full_authors:
        full_authors = ordered_authors
        full_authors_source = "paper_authors" if ordered_authors else None

    # Validate pdf_storage_path: if the file no longer exists, treat as None
    pdf_path = p.pdf_storage_path
    if pdf_path:
        resolved = storage.resolve_storage_path(pdf_path, settings.workspace)
        if resolved is None or not resolved.exists():
            pdf_path = None

    result = {
        "id": p.id,
        "internal_id": p.internal_id,
        "doi": p.doi,
        "apa_citation": sanitize_scholarly_markup(p.apa_citation),
        "title": sanitize_scholarly_markup(p.title),
        "title_zh": p.title_zh,
        "journal_name": p.journal_name,
        "year": p.year,
        "impact_factor": impact_factor,
        "jcr_quartile": jcr_quartile,
        "cas_quartile": cas_quartile,
        "emerging_quartile": emerging_quartile,
        "article_status": p.article_status,
        "processing_status": p.processing_status,
        "is_processing": p.processing_status == "processing",
        "processing_error": p.processing_error if detailed else None,
        "can_review": can_review_paper(p),
        "review_outcome": p.review_outcome,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": format_api_datetime(p.reviewed_at),
        "review_note": p.review_note if detailed else None,
        "has_pdf": p.has_pdf,
        "metadata_complete": p.metadata_complete,
        "can_generate": is_usable_for_generation(
            p,
            generation_requires_target_entity(load_domain(
                Path(settings.workspace) / settings.config_dir / "domain.yaml",
            )),
            get_target_label(),
        ),
        "status": p.article_status,
        "is_in_domain": p.is_in_domain,
        "cites_target": p.cites_target,
        "entity_label": p.entity_label,
        "corresponding_country": p.corresponding_country,
        "corresponding_province": p.corresponding_province,
        "corresponding_region": p.corresponding_region,
        "corresponding_city": p.corresponding_city,
        "corresponding_institution_type": p.corresponding_institution_type,
        "corresponding_department": p.corresponding_department,
        "subject_disease": p.subject_disease,
        "subject_diseases": p.subject_diseases,
        "research_topics": p.research_topics,
        "neuroscience_subtopics": p.neuroscience_subtopics,
        "paradigms": p.paradigms,
        "uses_multimodal": p.uses_multimodal,
        "multimodal_methods": p.multimodal_methods,
        "multimodal_relations": p.multimodal_relations,
        "classification_sources": p.classification_sources,
        "subject_age_mean": p.subject_age_mean,
        "study_design": p.study_design,
        "volume": p.volume,
        "issue": p.issue,
        "pages": p.pages,
        "first_authors": first_authors,
        "co_first_authors": [a for a in ordered_authors if a.get("is_first") and a.get("author_order", 0) > 0] if ordered_authors else [],
        "corresponding_authors": corresponding_authors,
        "authors": authors,
        "full_authors": full_authors,
        "full_authors_source": full_authors_source,
        "pdf_storage_path": pdf_path,
        "created_at": format_api_datetime(p.created_at),
        "updated_at": format_api_datetime(p.updated_at),
    }
    if detailed:
        result.update(
            {
                "abstract": p.abstract,
                "processing_error": p.processing_error,
                "review_note": p.review_note,
                "abstract_zh": p.abstract_zh,
                "conclusions_zh": p.conclusions_zh,
                "highlights_zh": p.highlights_zh,
                "keywords": p.keywords,
                "keywords_zh": p.keywords_zh,
                "research_fields": p.research_fields,
                "population": p.population,
                "paradigm": p.paradigm,
                "equipment_brand": p.equipment_brand,
                "channel_count": p.channel_count,
                "sample_size": p.sample_size,
                "tags": p.tags,
                "uses_target_software": p.uses_target_software,
                "pdf_storage_path": pdf_path,
                "pdf_text_extracted": p.pdf_text_extracted,
                "extracted_images": p.extracted_images,
                "pmid": p.pmid,
                "volume": p.volume,
                "issue": p.issue,
                "pages": p.pages,
                "device_citation_text": p.device_citation_text,
                "citation_error_type": p.citation_error_type,
                "corresponding_authors": corresponding_authors,
                "first_authors": first_authors,
                "co_first_authors": [a for a in ordered_authors if a.get("is_first") and a.get("author_order", 0) > 0] if ordered_authors else [],
                "authors": authors,
                "notes": p.notes,
                "accepted_date": p.accepted_date.isoformat() if p.accepted_date else None,
                "jcr_quartile": jcr_quartile,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
        )
    return result

def _impact_factor_subquery(session):
    """Subquery for the latest impact factor and quartiles per journal."""
    return _latest_metric_subquery(session, "listed_journal_metrics")

def _latest_metric_subquery(session, alias_name: str = "latest_journal_metrics"):
    """One latest-year metric row per journal, with deterministic same-year tie handling."""
    ranked = session.query(
        JournalMetric.journal_id.label("journal_id"),
        JournalMetric.impact_factor.label("impact_factor"),
        JournalMetric.jcr_quartile.label("jcr_quartile"),
        JournalMetric.cas_quartile.label("cas_quartile"),
        JournalMetric.emerging_quartile.label("emerging_quartile"),
        func.row_number().over(
            partition_by=JournalMetric.journal_id,
            order_by=(JournalMetric.year.desc(), JournalMetric.id.desc()),
        ).label("metric_rank"),
    ).subquery(f"ranked_{alias_name}")
    return session.query(
        ranked.c.journal_id,
        ranked.c.impact_factor,
        ranked.c.jcr_quartile,
        ranked.c.cas_quartile,
        ranked.c.emerging_quartile,
    ).filter(ranked.c.metric_rank == 1).subquery(alias_name)

def _apply_sort(query, sort_by: Optional[str], sort_order: str, if_subq):
    """Apply ORDER BY to a paper query."""
    sort_map = {
        "id": Paper.id,
        "internal_id": Paper.internal_id,
        "entity_label": Paper.entity_label,
        "title": Paper.title,
        "journal_name": Paper.journal_name,
        "year": Paper.year,
        "impact_factor": if_subq.c.impact_factor,
        "cas_quartile": if_subq.c.cas_quartile,
        "jcr_quartile": if_subq.c.jcr_quartile,
        "cites_target": Paper.cites_target,
        "updated_at": Paper.updated_at,
        "processing_status": Paper.processing_status,
        "review_outcome": Paper.review_outcome,
        "has_pdf": Paper.has_pdf,
        "metadata_complete": Paper.metadata_complete,
    }
    order_col = sort_map.get(sort_by, Paper.year)
    direction = desc if sort_order == "desc" else asc

    if sort_by != "year":
        query = query.order_by(direction(order_col), Paper.year.desc(), Paper.id.desc())
    else:
        query = query.order_by(direction(order_col), Paper.id.desc())
    return query

def _parse_ids(raw: Optional[str]) -> Optional[List[int]]:
    """Parse comma/space-separated IDs, e.g. '191, 494, 599' → [191, 494, 599]."""
    if not raw or not raw.strip():
        return None
    ids = []
    for token in raw.replace(",", " ").replace("，", " ").split():
        try:
            ids.append(int(token))
        except ValueError:
            continue
    if len(ids) > MAX_FILTERED_PAPER_IDS:
        raise HTTPException(status_code=422, detail="筛选论文数量不能超过 200 篇")
    return ids or None

def _extract_title(content: str, fallback: str = "") -> str:
    """Extract title from generated content, with safety fallback."""
    lines = content.strip().splitlines()
    for line in lines:
        s = line.strip()
        # Markdown heading
        if s.startswith("#"):
            raw = s.lstrip("#").strip()
            if raw and raw not in ("标题", "标题。", "(标题)", "标题："):
                return raw
            break
        # 【实体标签】... format (collection template)
        if s.startswith("【") and "】" in s:
            if s not in ("【实体】", "【实体合集】", "【课题】", "【课题合集】", "【合集主题】"):
                return s
    return fallback

def _build_base_query(
    session, q, journal, year_from, year_to, paper_ids=None, entity_label=None,
    affiliation=None, research_field=None, processing_status=None,
    review_outcome=None, has_pdf=None, metadata_complete=None, tag=None,
    country=None, province=None, city=None, institution_type=None, department=None,
    subject_disease=None, age_min=None, age_max=None, study_design=None,
    paradigm=None, has_multimodal=None, multimodal_technique=None,
    multimodal_timing=None, research_topic=None, neuroscience_subtopic=None,
    restricted_role: bool = False, can_view_excluded: bool = False,
    include_excluded: bool = False,
):
    query = session.query(Paper)

    if processing_status:
        requested_statuses = [s.strip() for s in processing_status.split(",") if s.strip()]
        invalid = sorted(set(requested_statuses) - PAPER_FILTER_PROCESSING_STATUSES)
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid processing_status value(s): {', '.join(invalid)}",
            )
    if review_outcome:
        requested_outcomes = [s.strip() for s in review_outcome.split(",") if s.strip()]
        invalid = sorted(set(requested_outcomes) - PAPER_FILTER_REVIEW_OUTCOMES)
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid review_outcome value(s): {', '.join(invalid)}",
            )
        # Review outcomes only exist after processing completes.  Filtering by
        # an outcome therefore also restricts the result to processed papers;
        # this keeps "未审核" from including pending/failed imports.
        query = query.filter(Paper.processing_status == "done")

    if restricted_role:
        query = query.filter(Paper.review_outcome == "approved")
    elif not can_view_excluded or (
        not include_excluded
        and (not review_outcome or "excluded" not in {
            item.strip() for item in review_outcome.split(",")
        })
    ):
        query = query.filter(Paper.review_outcome != "excluded")

    if paper_ids:
        query = query.filter(Paper.id.in_(paper_ids))
        return query  # ID exact match takes priority

    if q:
        # Parse comma/space-separated IDs from q (e.g. "191, 494, 599")
        parsed_ids = []
        text_terms: list[str] = []
        for token in q.replace(",", " ").replace("，", " ").split():
            try:
                parsed_ids.append(int(token))
            except ValueError:
                text_terms.append(token)

        # If pure numeric, search by internal_id first
        if not text_terms and parsed_ids:
            query = query.filter(Paper.internal_id.in_(parsed_ids))
        elif text_terms:
            # Text search: all terms must match (AND logic)
            # Each term can match any of these fields
            for term in text_terms:
                term_matches = or_(
                    Paper.title.ilike(f"%{term}%"),
                    Paper.title_zh.ilike(f"%{term}%"),
                    Paper.abstract.ilike(f"%{term}%"),
                    Paper.doi.ilike(f"%{term}%"),
                    Paper.journal_name.ilike(f"%{term}%"),
                    Paper.keywords.isnot(None) & Paper.keywords.cast(DbString).ilike(f"%{term}%"),
                    Paper.research_fields.isnot(None) & Paper.research_fields.cast(DbString).ilike(f"%{term}%"),
                    Paper.paradigm.isnot(None) & Paper.paradigm.cast(DbString).ilike(f"%{term}%"),
                    Paper.brain_regions.isnot(None) & Paper.brain_regions.cast(DbString).ilike(f"%{term}%"),
                    Paper.doi_meta.isnot(None) & Paper.doi_meta.cast(DbString).ilike(f"%{term}%"),
                )

                # Also search author names and affiliations
                aff_subq = text("""
                    SELECT DISTINCT pa.paper_id FROM paper_authors pa
                    JOIN authors a ON a.id = pa.author_id
                    LEFT JOIN affiliation_locations al ON al.id = a.canonical_affiliation_id
                    WHERE a.name LIKE :t OR a.name_zh LIKE :t
                    OR a.affiliation LIKE :t OR a.affiliation_zh LIKE :t
                    OR al.affiliation_zh LIKE :t OR al.canonical_name_en LIKE :t
                """)
                author_papers = [r[0] for r in session.execute(aff_subq, {"t": f"%{term}%"}).fetchall()]
                if author_papers:
                    term_matches = or_(term_matches, Paper.id.in_(author_papers))

                query = query.filter(term_matches)

    if affiliation:
        # Search authors table — both English AND Chinese affiliation columns
        aff_subq = (
            session.query(pa_tbl.c.paper_id)
            .join(Author, pa_tbl.c.author_id == Author.id)
            .filter(or_(
                Author.affiliation.ilike(f"%{affiliation}%"),
                Author.affiliation_zh.ilike(f"%{affiliation}%"),
            ))
            .subquery()
        )
        query = query.filter(
            or_(
                Paper.id.in_(aff_subq),
                Paper.first_authors.isnot(None) & Paper.first_authors.cast(DbString).ilike(f"%{affiliation}%"),
                Paper.doi_meta.isnot(None) & Paper.doi_meta.cast(DbString).ilike(f"%{affiliation}%"),
            )
        )
    if research_field:
        query = query.filter(
            Paper.research_fields.isnot(None),
            Paper.research_fields.cast(DbString).ilike(f"%{research_field}%"),
        )
    if research_topic:
        topic_values = _taxonomy_filter_values("research_topics", research_topic)
        query = query.filter(or_(
            *[
                Paper.research_topics.isnot(None) & json_array_contains(Paper.research_topics, value)
                for value in topic_values
            ],
            *[
                Paper.research_fields.isnot(None) & json_array_contains(Paper.research_fields, value)
                for value in topic_values
            ],
            *[
                Paper.research_fields.isnot(None)
                & Paper.research_fields.cast(DbString).ilike(f"%{value}%")
                for value in topic_values
            ],
        ))
    if neuroscience_subtopic:
        query = query.filter(
            Paper.neuroscience_subtopics.isnot(None),
            json_array_contains(Paper.neuroscience_subtopics, neuroscience_subtopic),
        )
    if entity_label:
        query = query.filter(Paper.entity_label == entity_label)
    if journal:
        query = query.filter(Paper.journal_name.ilike(f"%{journal}%"))
    if year_from is not None:
        query = query.filter(Paper.year >= year_from)
    if year_to is not None:
        query = query.filter(Paper.year <= year_to)
    if any((country, province, city, institution_type, department)):
        author_papers = (
            session.query(pa_tbl.c.paper_id)
            .join(Author, pa_tbl.c.author_id == Author.id)
            .filter(pa_tbl.c.is_corresponding.is_(True))
        )
        country_en = COUNTRY_ZH_TO_EN.get(country, country) if country else ""
        if country_en:
            author_papers = author_papers.filter(Author.country == country_en)
        if province:
            author_papers = author_papers.filter(Author.province == province)
        if city:
            author_papers = author_papers.filter(Author.city == city)
        if institution_type:
            author_papers = author_papers.filter(Author.institution_type == institution_type)
        if department:
            department_rows = (
                session.query(Author.department, Author.institution_type)
                .filter(Author.department.isnot(None), func.trim(Author.department) != "")
                .distinct()
                .all()
            )
            matching_pairs = [
                (raw, raw_type) for raw, raw_type in department_rows
                if normalize_department(raw, raw_type) == department
            ]
            if matching_pairs:
                author_papers = author_papers.filter(or_(*[
                    and_(
                        Author.department == raw,
                        Author.institution_type == raw_type if raw_type is not None
                        else Author.institution_type.is_(None),
                    )
                    for raw, raw_type in matching_pairs
                ]))
            else:
                author_papers = author_papers.filter(Author.department == department)
        query = query.filter(Paper.id.in_(author_papers))
    if processing_status:
        statuses = [s.strip() for s in processing_status.split(",") if s.strip()]
        if statuses:
            query = query.filter(Paper.processing_status.in_(statuses))
    if review_outcome:
        outcomes = [s.strip() for s in review_outcome.split(",") if s.strip()]
        if outcomes:
            query = query.filter(Paper.review_outcome.in_(outcomes))
    if has_pdf is not None:
        query = query.filter(Paper.has_pdf == has_pdf)
    if metadata_complete is not None:
        query = query.filter(Paper.metadata_complete == metadata_complete)
    if tag:
        tag_values = _taxonomy_filter_values("tags", tag)
        # Match native JSON arrays without assuming a database-specific JSON type.
        query = query.filter(
            Paper.tags.isnot(None),
            or_(*(json_array_contains(Paper.tags, value) for value in tag_values)),
        )
    if subject_disease:
        disease_values = _taxonomy_filter_values("diseases", subject_disease)
        query = query.filter(or_(
            *[
                Paper.subject_diseases.isnot(None) & json_array_contains(Paper.subject_diseases, value)
                for value in disease_values
            ],
            # Backward compatibility for databases not backfilled yet.
            Paper.subject_diseases.is_(None) & Paper.subject_disease.in_(disease_values),
        ))
    if age_min is not None:
        query = query.filter(Paper.subject_age_mean >= age_min)
    if age_max is not None:
        query = query.filter(Paper.subject_age_mean <= age_max)
    if study_design:
        query = query.filter(Paper.study_design.in_(_taxonomy_filter_values("study_design", study_design)))
    if paradigm:
        paradigm_values = _taxonomy_filter_values("paradigms", paradigm)
        legacy_paradigm_ids = [
            paper_id for paper_id, value in session.query(Paper.id, Paper.paradigm)
            .filter(Paper.paradigms.is_(None), Paper.paradigm.isnot(None)).all()
            if paradigm in normalize_paradigms(value)
        ]
        query = query.filter(or_(
            *[
                Paper.paradigms.isnot(None) & json_array_contains(Paper.paradigms, value)
                for value in paradigm_values
            ],
            Paper.id.in_(legacy_paradigm_ids),
        ))
    if has_multimodal is not None:
        query = query.filter(or_(
            Paper.uses_multimodal == has_multimodal,
            Paper.uses_multimodal.is_(None) & (
                Paper.multimodal_techniques.isnot(None)
                & json_field_equals(Paper.multimodal_techniques, "$.has_multimodal", has_multimodal)
            ),
        ))
    if multimodal_technique:
        technique_values = _taxonomy_filter_values("multimodal", multimodal_technique)
        legacy_technique_ids = [
            paper_id for paper_id, value in session.query(Paper.id, Paper.multimodal_techniques)
            .filter(Paper.multimodal_methods.is_(None), Paper.multimodal_techniques.isnot(None)).all()
            if multimodal_technique in normalize_multimodal_techniques(value)
        ]
        query = query.filter(or_(
            *[
                Paper.multimodal_methods.isnot(None)
                & json_array_contains(Paper.multimodal_methods, value)
                for value in technique_values
            ],
            Paper.id.in_(legacy_technique_ids),
        ))
    if multimodal_timing:
        timing_values = _taxonomy_filter_values("multimodal_timing", multimodal_timing)
        technique_values = _taxonomy_filter_values("multimodal", multimodal_technique) if multimodal_technique else [None]
        relation_filter = or_(*[
            json_array_of_objects_field_equals(
                Paper.multimodal_relations,
                "timing",
                timing_value,
                "method" if technique_value else None,
                technique_value,
            )
            for timing_value in timing_values
            for technique_value in technique_values
        ])
        query = query.filter(
            Paper.multimodal_relations.isnot(None),
            relation_filter,
        )
    return query
