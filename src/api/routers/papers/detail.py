"""Paper detail API routes."""
import logging

from fastapi import APIRouter

from paper_library.models import Author
from paper_library.models import AuthorIdentifier
from paper_library.models import AuthorIdentityEvent
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from paper_library.models import JournalMetric
from paper_library.models import Paper
from paper_library.models import PaperAuthorMention
from pathlib import Path
from fastapi import Request
from paper_library.author_identity import add_author_identifier
from paper_library.author_identity import contains_han
import datetime
from paper_library.journal_enrichment import enrich_paper_journal
from paper_library.abstract_enrichment import extract_crossref_abstract
from core.crossref_api import fetch_metadata_by_doi
from paper_library.citation import format_apa_author_list
from paper_library.citation import format_apa_person_name
from sqlalchemy import func
from ...dependencies import get_current_user
from paper_library.cross_db import get_draft_ids_for_paper
from paper_library.db import get_session
from paper_library.crud import get_paper_by_doi
from core.entity_detector import get_target_label
from paper_library.paper_lifecycle import (
    approve_paper,
    complete_processing,
    exclude_paper,
    refresh_metadata_complete,
    refresh_pdf_flag,
    reset_review,
)
from paper_library.doi import normalize_doi
from paper_library.author_identity import normalize_identifier
from paper_library.tag_enrichment import normalize_paradigm_selection
from sqlalchemy import or_
from paper_library.models import paper_authors as pa_tbl
from ...dependencies import require_paper_editor
from ...dependencies import require_reviewer
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from paper_library.citation import sanitize_apa_journal_name
from paper_library.citation import sanitize_scholarly_markup
from sqlalchemy import select
from ...config import settings
from tools import storage
from sqlalchemy import text
import time
from paper_library.cross_db import unlink_paper_from_all_drafts
from paper_library.author_identity import upsert_paper_author_mention

from .common import (
    _PAPER_AUTHOR_ROLE_FLAGS,
    _extract_authors_from_doi_meta,
    _get_ordered_authors,
    _get_jcr_matcher,
    _paper_to_dict,
    ensure_paper_visible,
)

from .schemas import (
    BatchDeleteBody,
    DeletePaperBody,
    ReviewRequest,
    UpdateDoiRequest,
    UpdatePaperRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/{paper_id}")
def get_paper(
    paper_id: int,
    user: dict = Depends(get_current_user),
):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        p = ensure_paper_visible(session.get(Paper, paper_id), user)

        # compute impact_factor and quartiles for detail view
        impact_factor = None
        jcr_quartile = None
        cas_quartile = None
        emerging_quartile = None
        if p.journal_id:
            metric = (
                session.query(JournalMetric)
                .filter(JournalMetric.journal_id == p.journal_id)
                .order_by(JournalMetric.year.desc())
                .first()
            )
            if metric:
                impact_factor = metric.impact_factor
                jcr_quartile = metric.jcr_quartile
                cas_quartile = metric.cas_quartile
                emerging_quartile = metric.emerging_quartile

        result = _paper_to_dict(p, impact_factor=impact_factor, jcr_quartile=jcr_quartile, cas_quartile=cas_quartile, emerging_quartile=emerging_quartile, detailed=True, session=session)
        # Include draft IDs for this paper
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as ds:
            result["draft_ids"] = get_draft_ids_for_paper(p.id, ds)
        return result

def _upsert_author_from_form(session, a: dict) -> Author:
    """Resolve a person only from explicit ID/ORCID; never from name alone."""
    author_id = a.get("author_id")
    name = (a.get("name_en") or a.get("name") or "").strip()
    name_zh = (a.get("name_zh") or "").strip()
    orcid = (a.get("orcid") or "").strip()
    email = (a.get("email") or "").strip()

    if name and contains_han(name):
        raise HTTPException(status_code=422, detail="英文姓名不能包含中文字符")
    if name_zh and not contains_han(name_zh):
        raise HTTPException(status_code=422, detail="中文姓名必须包含中文字符")

    if author_id:
        existing = session.get(Author, int(author_id))
        if existing:
            try:
                if orcid:
                    add_author_identifier(
                        session, author_id=existing.id, identifier_type="orcid",
                        identifier_value=orcid, source_type="manual_edit",
                    )
                if email:
                    add_author_identifier(
                        session, author_id=existing.id, identifier_type="email",
                        identifier_value=email, source_type="manual_edit",
                    )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return existing

    if orcid:
        try:
            normalized_orcid = normalize_identifier("orcid", orcid)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        legacy_author = session.scalar(select(Author).where(Author.orcid == normalized_orcid))
        evidence_author = session.scalar(
            select(Author)
            .join(AuthorIdentifier, AuthorIdentifier.author_id == Author.id)
            .where(
                AuthorIdentifier.identifier_type == "orcid",
                AuthorIdentifier.normalized_value == normalized_orcid,
            )
        )
        if legacy_author and evidence_author and legacy_author.id != evidence_author.id:
            raise HTTPException(status_code=422, detail=f"ORCID 身份证据冲突: {normalized_orcid}")
        existing = evidence_author or legacy_author
        if existing:
            return existing

    if not name:
        raise HTTPException(status_code=422, detail="作者姓名不能为空")

    author = Author(
        name=name,
        name_zh=name_zh or None,
        orcid=normalize_identifier("orcid", orcid) if orcid else None,
    )
    session.add(author)
    session.flush()
    if orcid:
        add_author_identifier(
            session,
            author_id=author.id,
            identifier_type="orcid",
            identifier_value=orcid,
            source_type="manual_edit",
        )
    if email:
        add_author_identifier(
            session,
            author_id=author.id,
            identifier_type="email",
            identifier_value=email,
            source_type="manual_edit",
        )
    return author

def _upsert_paper_author_link(
    session,
    paper_id: int,
    author: Author,
    author_order: int,
    is_first: bool,
    is_corresponding: bool,
    author_data: dict,
    *,
    update_order: bool = False,
):
    """Upsert roles and a paper snapshot without changing the person entity."""
    author_id = author.id
    existing = session.execute(
        select(pa_tbl).where(pa_tbl.c.paper_id == paper_id, pa_tbl.c.author_id == author_id)
    ).first()
    if existing:
        if update_order:
            session.execute(
                sa_update(pa_tbl)
                .where(pa_tbl.c.paper_id == paper_id, pa_tbl.c.author_id == author_id)
                .values(
                    author_order=author_order,
                    is_first=is_first,
                    is_corresponding=is_corresponding,
                )
            )
        else:
            # Role-only edits must not reinterpret the role-list index as the
            # complete publication author order.
            session.execute(
                sa_update(pa_tbl)
                .where(pa_tbl.c.paper_id == paper_id, pa_tbl.c.author_id == author_id)
                .values(
                    is_first=or_(pa_tbl.c.is_first, is_first),
                    is_corresponding=or_(pa_tbl.c.is_corresponding, is_corresponding),
                )
            )
    else:
        if not update_order:
            max_order = session.scalar(
                select(func.max(pa_tbl.c.author_order)).where(pa_tbl.c.paper_id == paper_id)
            )
            author_order = 0 if max_order is None else max_order + 1
        session.execute(
            pa_tbl.insert().values(
                paper_id=paper_id, author_id=author_id,
                author_order=author_order, is_first=is_first, is_corresponding=is_corresponding,
            )
        )

    submitted_name = (
        author_data.get("name_en") or author_data.get("name") or author.name or ""
    ).strip()
    submitted_name_zh = (author_data.get("name_zh") or "").strip()
    legacy_affiliation = (author_data.get("affiliation") or "").strip()
    affiliation_en = (author_data.get("affiliation_en") or "").strip()
    affiliation_zh = (author_data.get("affiliation_zh") or "").strip()
    if legacy_affiliation and not affiliation_en and not affiliation_zh:
        if contains_han(legacy_affiliation):
            affiliation_zh = legacy_affiliation
        else:
            affiliation_en = legacy_affiliation
    if not submitted_name or contains_han(submitted_name):
        raise HTTPException(status_code=422, detail="论文英文署名不能为空且不能包含中文字符")
    if submitted_name_zh and not contains_han(submitted_name_zh):
        raise HTTPException(status_code=422, detail="论文中文姓名必须包含中文字符")
    if affiliation_en and contains_han(affiliation_en):
        raise HTTPException(status_code=422, detail="英文单位不能包含中文字符")
    if affiliation_zh and not contains_han(affiliation_zh):
        raise HTTPException(status_code=422, detail="中文单位必须包含中文字符")
    upsert_paper_author_mention(
        session,
        paper_id=paper_id,
        author_id=author_id,
        name_raw=submitted_name,
        name_zh=submitted_name_zh or None,
        affiliation_raw=affiliation_en or None,
        affiliation_zh=affiliation_zh or None,
        email_raw=(author_data.get("email") or "").strip() or None,
        orcid_raw=(author_data.get("orcid") or "").strip() or None,
        source_type="manual_edit",
        source_ref=f"paper:{paper_id}",
        verification_status=author_data.get("verification_status") or "manual",
        replace_empty=True,
    )

def _sync_full_author_list(session, paper_id: int, values: list[dict]) -> None:
    """Replace one paper's complete ordered author list transactionally."""
    resolved: list[tuple[Author, dict]] = []
    seen_ids: set[int] = set()
    for item in values:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="作者列表项目必须是对象")
        author = _upsert_author_from_form(session, item)
        if author.id in seen_ids:
            raise HTTPException(status_code=422, detail=f"同一作者重复出现: {author.id}")
        seen_ids.add(author.id)
        resolved.append((author, item))

    existing_ids = set(session.scalars(
        select(pa_tbl.c.author_id).where(pa_tbl.c.paper_id == paper_id)
    ))
    removed_ids = existing_ids - seen_ids
    for author_id in removed_ids:
        session.execute(
            sa_delete(PaperAuthorMention).where(
                PaperAuthorMention.paper_id == paper_id,
                PaperAuthorMention.author_id == author_id,
            )
        )
        session.execute(
            pa_tbl.delete().where(
                pa_tbl.c.paper_id == paper_id,
                pa_tbl.c.author_id == author_id,
            )
        )
        session.add(AuthorIdentityEvent(
            event_type="unlink_from_paper",
            source_author_id=author_id,
            paper_id=paper_id,
            reason="Removed by explicit complete-author-list edit",
            actor="paper_api",
        ))

    for index, (author, item) in enumerate(resolved):
        _upsert_paper_author_link(
            session,
            paper_id,
            author,
            author_order=index,
            is_first=bool(item.get("is_first")),
            is_corresponding=bool(item.get("is_corresponding")),
            author_data=item,
            update_order=True,
        )

@router.post("/{paper_id}/update-doi")
def update_paper_doi(
    paper_id: int,
    body: UpdateDoiRequest,
    user: dict = Depends(require_paper_editor),
):
    """更新论文 DOI：从 CrossRef 拉取新元数据，更新论文记录。
    用于将预印版论文更新为正式版（或纠正错误 DOI）。
    """
    normalized = normalize_doi(body.new_doi)
    if not normalized:
        raise HTTPException(status_code=400, detail="DOI 格式无效")

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")

        # Check if new DOI already exists on another paper
        existing = get_paper_by_doi(session, normalized)
        if existing and existing.id != paper_id:
            raise HTTPException(
                status_code=409,
                detail=f"DOI 已被论文 #{existing.internal_id or existing.id} 使用",
            )

        # Fetch CrossRef metadata
        time.sleep(0.5)
        meta = fetch_metadata_by_doi(normalized, structured=True)
        if not meta or meta.get("_error"):
            if meta and meta.get("_error"):
                logger.warning("CrossRef lookup failed while updating DOI %s: %s", normalized, meta["_error"])
                raise HTTPException(status_code=503, detail="CrossRef 查询暂时失败，请稍后重试")
            raise HTTPException(status_code=422, detail="CrossRef 未找到该 DOI 的元数据")

        raw = meta.get("raw") or {}

        # Update paper fields from CrossRef
        old_doi = paper.doi
        paper.doi = normalized
        paper.doi_meta = raw
        if meta.get("title"):
            paper.title = meta["title"]
        if meta.get("year"):
            paper.year = meta["year"]
        if raw.get("container-title"):
            paper.journal_name = raw["container-title"][0]
        if raw.get("volume") is not None:
            paper.volume = str(raw["volume"])
        if raw.get("issue") is not None:
            paper.issue = str(raw["issue"])
        if raw.get("page") is not None:
            paper.pages = str(raw["page"])
        abstract = extract_crossref_abstract(meta)
        if abstract:
            paper.abstract = abstract

        # Extract published_date from CrossRef (priority: published-print > published-online > issued)
        for date_key in ["published-print", "published-online", "issued"]:
            date_obj = raw.get(date_key, {})
            if isinstance(date_obj, dict):
                parts = date_obj.get("date-parts", [[]])[0]
                if parts:
                    if len(parts) == 2:
                        paper.published_date = f"{parts[0]}-{parts[1]:02d}-01"
                    elif len(parts) >= 3:
                        paper.published_date = f"{parts[0]}-{parts[1]:02d}-{parts[2]:02d}"
                    if paper.published_date:
                        paper.published_date_source = "crossref"
                    break

        paper.source = "doi_update"

        # Re-build APA citation
        from paper_library.crud import _build_apa_for_paper_data
        paper_data = {
            "doi": normalized, "title": paper.title, "year": paper.year,
            "journal_name": paper.journal_name, "volume": paper.volume,
            "issue": paper.issue, "pages": paper.pages,
        }
        _build_apa_for_paper_data(paper_data, raw)
        if paper_data.get("apa_citation"):
            paper.apa_citation = paper_data["apa_citation"]

        # Re-match JCR journal info
        try:
            matcher = _get_jcr_matcher()
            if matcher:
                enrich_paper_journal(paper, session, matcher)
        except Exception:
            logger.warning('处理失败，已跳过该项（update_paper_doi）', exc_info=True)
            pass

        complete_processing(paper)
        refresh_pdf_flag(paper, settings.workspace)
        session.flush()
        session.expire(paper, ["authors"])
        refresh_metadata_complete(paper)

        session.commit()

        return {
            "paper_id": paper.id,
            "old_doi": old_doi,
            "new_doi": normalized,
            "title": paper.title,
            "journal_name": paper.journal_name,
            "year": paper.year,
            "message": "DOI 更新成功",
        }

@router.patch("/{paper_id}")
def update_paper(
    paper_id: int,
    body: UpdatePaperRequest,
    user: dict = Depends(require_paper_editor),
):
    """更新论文的可编辑字段。academic 角色可编辑全部，其他角色只读。"""
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        updates = body.model_dump(exclude_unset=True)
        if "paradigms" in updates:
            # Canonical aliases are merged, while meaningful editor-created
            # paradigms remain available.  Empty placeholders never enter the
            # shared filter catalog.
            updates["paradigms"] = normalize_paradigm_selection(updates["paradigms"])
        updated_fields = []
        classification_dimensions = {
            "subject_diseases": "subject_diseases",
            "research_topics": "research_topics",
            "neuroscience_subtopics": "neuroscience_subtopics",
            "study_design": "study_design",
            "paradigms": "paradigms",
            "uses_multimodal": "multimodal",
            "multimodal_methods": "multimodal",
            "multimodal_relations": "multimodal_relations",
        }
        manually_updated_dimensions: set[str] = set()

        requested_methods = updates.get("multimodal_methods", paper.multimodal_methods or [])

        requested_method_set = {
            method.strip() for method in requested_methods
            if isinstance(method, str) and method.strip()
        }
        if "multimodal_relations" in updates:
            normalized_relations: list[dict[str, str]] = []
            seen_relations: set[tuple[str, str]] = set()
            for relation in updates["multimodal_relations"] or []:
                method = str(relation.get("method") or "").strip()
                timing = relation.get("timing")
                if not method or method not in requested_method_set:
                    raise HTTPException(status_code=422, detail=f"联用方式对应的技术无效: {method or '空值'}")
                key = (method, timing)
                if key not in seen_relations:
                    normalized_relations.append({"method": method, "timing": timing})
                    seen_relations.add(key)
            updates["multimodal_relations"] = normalized_relations

        for field, value in updates.items():
            if field == "authors":
                if value is None:
                    raise HTTPException(status_code=422, detail="完整作者列表不能为 null")
                _sync_full_author_list(session, paper_id, value)
                updated_fields.append(field)
                continue
            # 作者字段特殊处理：同步到 authors + paper_authors 表
            if field in ("first_authors", "corresponding_authors"):
                role_flag = "is_first" if field == "first_authors" else "is_corresponding"
                if role_flag not in _PAPER_AUTHOR_ROLE_FLAGS:
                    raise AssertionError("paper author role column must be allowlisted")
                # 清除该角色的旧标记
                session.execute(
                    text(f"UPDATE paper_authors SET {role_flag} = :false_value WHERE paper_id = :pid"),
                    {"pid": paper_id, "false_value": False},
                )
                if value and isinstance(value, list):
                    for idx, a in enumerate(value):
                        if not isinstance(a, dict):
                            continue
                        author = _upsert_author_from_form(session, a)
                        _upsert_paper_author_link(
                            session, paper_id, author,
                            author_order=idx,
                            is_first=(role_flag == "is_first"),
                            is_corresponding=(role_flag == "is_corresponding"),
                            author_data=a,
                        )
                # 同步更新 JSON 列
                if hasattr(paper, field):
                    setattr(paper, field, value)
                updated_fields.append(field)
                continue

            if hasattr(paper, field):
                field_changed = getattr(paper, field) != value
                setattr(paper, field, value if value is not None else None)
                updated_fields.append(field)
                if field in classification_dimensions and field_changed:
                    manually_updated_dimensions.add(classification_dimensions[field])

        if updates.get("uses_multimodal") is False and "multimodal_methods" not in updates:
            paper.multimodal_methods = []
            if "multimodal_methods" not in updated_fields:
                updated_fields.append("multimodal_methods")

        if updates.get("uses_multimodal") is False:
            paper.multimodal_relations = []
            if "multimodal_relations" not in updated_fields:
                updated_fields.append("multimodal_relations")

        if "multimodal_methods" in updates and "multimodal_relations" not in updates:
            # 删除技术时同步清理它的联用方式，避免产生悬空关系。
            filtered_relations = [
                relation for relation in (paper.multimodal_relations or [])
                if isinstance(relation, dict) and relation.get("method") in requested_method_set
            ]
            if filtered_relations != (paper.multimodal_relations or []):
                paper.multimodal_relations = filtered_relations
                updated_fields.append("multimodal_relations")

        if manually_updated_dimensions:
            sources = dict(paper.classification_sources or {})
            username = user.get("username") or user.get("name") or "unknown"
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for dimension in manually_updated_dimensions:
                sources[dimension] = {
                    "source": "manual",
                    "updated_by": username,
                    "updated_at": timestamp,
                }
            paper.classification_sources = sources
            updated_fields.append("classification_sources")

        session.flush()
        session.expire(paper, ["authors"])
        refresh_metadata_complete(paper)
        paper.updated_at = func.now()
        session.commit()
        return {"paper_id": paper_id, "updated": updated_fields}

def _stage_paper_deletion(session, paper: Paper) -> tuple[Path | None, Path, list[int]]:
    """Stage all database changes required to delete one paper.

    No commit and no filesystem mutation happens here.  Keeping file deletion
    outside the database transaction prevents a failed FK check from deleting
    a PDF/image directory while leaving the paper row behind.
    """
    paper_id = paper.id
    author_ids = list(session.scalars(
        select(pa_tbl.c.author_id).where(pa_tbl.c.paper_id == paper_id)
    ))
    pdf_path = (
        storage.resolve_storage_path(paper.pdf_storage_path, settings.workspace)
        if paper.pdf_storage_path else None
    )
    image_dir = storage.get_paper_image_dir(paper_id, settings.workspace)

    # Stage relation cleanup in the same transaction as the paper deletion.
    unlink_paper_from_all_drafts(paper_id, session)

    # Preserve reusable identity evidence for authors linked to other papers,
    # while removing the soon-to-be-deleted paper as its provenance pointer.
    session.execute(
        sa_update(AuthorIdentifier)
        .where(AuthorIdentifier.source_paper_id == paper_id)
        .values(source_paper_id=None)
    )
    session.execute(
        sa_delete(PaperAuthorMention).where(PaperAuthorMention.paper_id == paper_id)
    )
    session.execute(sa_delete(pa_tbl).where(pa_tbl.c.paper_id == paper_id))
    return pdf_path, image_dir, author_ids

def _delete_orphan_authors(session, candidate_author_ids: list[int]) -> list[int]:
    """Delete authors left with no paper links or paper-specific mentions.

    Identifier rows belong to the author identity and are removed only when the
    author itself is truly orphaned.  Append-only identity events intentionally
    remain as historical audit records.
    """
    deleted: list[int] = []
    for author_id in sorted(set(candidate_author_ids)):
        has_paper = session.execute(
            select(pa_tbl.c.paper_id).where(pa_tbl.c.author_id == author_id).limit(1)
        ).first()
        has_mention = session.execute(
            select(PaperAuthorMention.id)
            .where(PaperAuthorMention.author_id == author_id)
            .limit(1)
        ).first()
        if has_paper or has_mention:
            continue
        session.execute(
            sa_delete(AuthorIdentifier).where(AuthorIdentifier.author_id == author_id)
        )
        result = session.execute(sa_delete(Author).where(Author.id == author_id))
        if result.rowcount:
            deleted.append(author_id)
    return deleted

def _delete_paper_files(pdf_path: Path | None, image_dir: Path) -> None:
    """Best-effort file cleanup after all database commits succeed."""
    if pdf_path:
        storage.safe_delete_file(pdf_path)
    storage.safe_delete_dir(image_dir)

@router.delete("/batch")
def batch_delete_papers(
    body: BatchDeleteBody,
    request: Request,
    user: dict = Depends(require_paper_editor),
):
    deleted = 0
    not_found = []
    orphan_author_ids: list[int] = []
    pending_file_deletions: list[tuple[Path | None, Path]] = []
    paper_ids_to_delete: list[int] = []
    internal_ids_to_delete: list[int] = []

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        for paper_id in dict.fromkeys(body.paper_ids):
            p = session.get(Paper, paper_id)
            if p is None:
                not_found.append(paper_id)
                continue
            pdf_path, image_dir, author_ids = _stage_paper_deletion(session, p)
            pending_file_deletions.append((pdf_path, image_dir))
            orphan_author_ids.extend(author_ids)
            paper_ids_to_delete.append(paper_id)
            if p.internal_id is not None:
                internal_ids_to_delete.append(p.internal_id)
            deleted += 1

        try:
            deleted_orphan_author_ids = _delete_orphan_authors(session, orphan_author_ids)
            if paper_ids_to_delete:
                session.execute(sa_delete(Paper).where(Paper.id.in_(paper_ids_to_delete)))
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="所选论文仍被其他数据引用，未删除论文或相关文件",
            ) from exc

    request.state.audit_details = {
        "paper_ids": paper_ids_to_delete,
        "paper_internal_ids": internal_ids_to_delete,
        "delete_reason": body.reason,
    }
    for pdf_path, image_dir in pending_file_deletions:
        _delete_paper_files(pdf_path, image_dir)
    return {
        "deleted": deleted,
        "not_found": not_found,
        "orphan_authors_deleted": len(deleted_orphan_author_ids),
    }

@router.delete("/{paper_id}")
def delete_paper(
    paper_id: int,
    body: DeletePaperBody,
    request: Request,
    user: dict = Depends(require_paper_editor),
):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        p = session.get(Paper, paper_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Paper not found")
        internal_id = p.internal_id
        paper_title = p.title
        pdf_path, image_dir, author_ids = _stage_paper_deletion(session, p)
        try:
            orphan_author_ids = _delete_orphan_authors(session, author_ids)
            session.execute(sa_delete(Paper).where(Paper.id == paper_id))
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="论文仍被其他数据引用，未删除论文或相关文件",
            ) from exc

    request.state.audit_details = {
        "paper_internal_ids": [internal_id] if internal_id is not None else [],
        "paper_title": paper_title,
        "delete_reason": body.reason,
        "orphan_authors_deleted": len(orphan_author_ids),
    }
    _delete_paper_files(pdf_path, image_dir)
    return {
        "deleted": True,
        "paper_id": paper_id,
        "orphan_authors_deleted": len(orphan_author_ids),
    }

def _to_apa_author(name: str) -> str:
    """Convert 'Given Family' → 'Family, G.' for APA 7th."""
    return format_apa_person_name(name)

def _build_apa_citation(paper: Paper, session=None) -> str:
    """Build APA 7th edition citation from all available author sources."""
    doi_authors = _extract_authors_from_doi_meta(paper.doi_meta)
    apa_authors: list[str] = []

    if doi_authors:
        # doi_meta 有结构化作者（given+family）→ Western 名解析
        author_names = [
            a.get("name", "") for a in doi_authors
            if a.get("name") and not contains_han(a.get("name", ""))
        ]
        apa_authors = [_to_apa_author(n) for n in author_names if n]

    if not apa_authors:
        # 无 doi_meta 作者 → 使用该论文完整、有序的逐论文作者快照。
        ordered = _get_ordered_authors(session, paper.id) if session else []
        apa_authors = [
            format_apa_person_name(author.get("name", ""))
            for author in ordered
            if author.get("name")
        ]

    author_part = format_apa_author_list(apa_authors)

    year_part = f"({paper.year})." if paper.year else ""
    title_part = f"{sanitize_scholarly_markup(paper.title) or '(No title)'}."
    journal_part = sanitize_apa_journal_name(paper.journal_name)
    if not journal_part and paper.journal is not None:
        journal_part = sanitize_apa_journal_name(paper.journal.name)

    vol_issue_pages = ""
    if paper.volume:
        vol_issue_pages = str(paper.volume)
        if paper.issue:
            vol_issue_pages += f"({paper.issue})"
        if paper.pages:
            vol_issue_pages += f", {paper.pages}"
        vol_issue_pages += "."
    elif paper.pages:
        vol_issue_pages = f"{paper.pages}."

    doi_part = f" https://doi.org/{paper.doi}" if paper.doi else ""

    parts = [p for p in [author_part, year_part, title_part, journal_part, vol_issue_pages + doi_part] if p]
    return " ".join(parts).replace("  ", " ").strip()

@router.post("/{paper_id}/review")
def review_paper(
    paper_id: int,
    req: ReviewRequest,
    user: dict = Depends(require_reviewer),
):
    """审核、排除或撤销一篇论文的准入结论。"""
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="Paper not found")

        if req.decision in {"approve", "exclude"} and paper.processing_status != "done":
            raise HTTPException(
                status_code=409,
                detail="论文尚未处理完成，暂不能审核",
            )

        if req.entity_label is not None:
            paper.entity_label = req.entity_label if req.entity_label != "" else None

        reviewer = str(user.get("username") or user.get("name") or "unknown")
        if req.decision == "approve":
            target_label = get_target_label()
            if target_label and paper.entity_label == target_label and not paper.internal_id:
                paper.internal_id = session.query(func.coalesce(func.max(Paper.internal_id), 0)).scalar() + 1
            approve_paper(paper, reviewer, req.note)
        elif req.decision == "exclude":
            exclude_paper(paper, reviewer, req.note)
        else:
            reset_review(paper)

        # Rename PDF if it was using placeholder (9999 = no internal_id)
        if req.decision == "approve" and paper.pdf_storage_path:
            from paper_library.pdf_import import make_pdf_filename
            old_path = Path(paper.pdf_storage_path)
            if not old_path.is_absolute():
                old_path = Path(settings.workspace) / old_path
            if old_path.exists():
                new_name = make_pdf_filename(
                    paper.id, paper.internal_id,
                    paper.year, paper.title or "paper"
                )
                new_path = old_path.parent / new_name
                try:
                    old_path.rename(new_path)
                    paper.pdf_storage_path = str(new_path)
                except OSError:
                    pass  # rename failed, keep old path

        refresh_pdf_flag(paper, settings.workspace)

        session.commit()
        return {
            "paper_id": paper_id,
            "internal_id": paper.internal_id,
            "entity_label": paper.entity_label,
            "processing_status": paper.processing_status,
            "can_review": paper.processing_status == "done",
            "review_outcome": paper.review_outcome,
            "reviewed_by": paper.reviewed_by,
            "reviewed_at": paper.reviewed_at.isoformat() if paper.reviewed_at else None,
            "review_note": paper.review_note,
            "has_pdf": paper.has_pdf,
            "metadata_complete": paper.metadata_complete,
        }
