"""Draft CRUD, status transitions, and response DTO construction."""

from __future__ import annotations

import datetime
from pathlib import Path

from sqlalchemy import or_, select, text

from paper_library.cross_db import (
    draft_has_other_drafts_for_paper,
    get_paper_ids_for_draft,
    get_papers_for_draft,
    unlink_all_papers_from_draft,
)
from paper_library.models import ArticleDraft, Paper, draft_papers
from paper_library.time_utils import format_api_datetime
from tools import storage

from .draft_images import draft_image_context


class DraftNotFoundError(LookupError):
    pass


class InvalidDraftStatusTransition(ValueError):
    pass


EDITABLE_STATUS_TRANSITIONS = {
    "draft": {"draft", "approved", "rejected"},
    "approved": {"approved", "draft", "rejected"},
    "rejected": {"rejected", "draft", "approved"},
    # Historical rows may still be marked reviewed; they can be normalized through review.
    "reviewed": {"reviewed", "draft", "approved", "rejected"},
}


def draft_to_dict(draft: ArticleDraft, session, *, include_paper: bool = False, workspace: str = ".") -> dict:
    papers = get_papers_for_draft(draft.id, session)
    first = papers[0] if papers else None
    result = {
        "id": draft.id,
        "draft_type": draft.draft_type,
        "title": draft.title,
        "title_zh": draft.title_zh,
        "status": draft.status,
        "reviewer": draft.reviewer,
        "reviewed_at": format_api_datetime(draft.reviewed_at),
        "review_notes": draft.review_notes,
        "published_at": format_api_datetime(draft.published_at),
        "created_at": format_api_datetime(draft.created_at),
        "updated_at": format_api_datetime(draft.updated_at),
        "paper_title": first.title if first else None,
        "paper_id": first.id if first else None,
        "paper_internal_id": first.internal_id if first else None,
        "paper_ids": [
            {"id": paper.id, "internal_id": paper.internal_id, "title": paper.title[:80] if paper.title else ""}
            for paper in papers
        ],
    }
    if include_paper:
        image_context = draft_image_context(draft, session, workspace, papers=papers)
        result.update({
            "content": draft.content,
            "meta": draft.meta,
            "paper_title": image_context["paper_title"],
            "paper_abstract": image_context["paper_abstract"],
            "paper_conclusions_zh": image_context["paper_conclusions_zh"],
            "paper_images": image_context["paper_images"],
            "paper_selected_images": image_context["paper_selected_images"],
        })
    return result


def list_drafts(session, *, statuses: list[str], query_text: str | None, page: int, page_size: int, role: str = "admin") -> dict:
    query = session.query(ArticleDraft)
    if role == "viewer":
        excluded_draft_ids = (
            select(draft_papers.c.draft_id)
            .join(Paper, Paper.id == draft_papers.c.paper_id)
            .where(Paper.review_outcome == "excluded")
        )
        query = query.filter(~ArticleDraft.id.in_(excluded_draft_ids))
    if statuses:
        query = query.filter(ArticleDraft.status.in_(statuses))
    if query_text:
        linked_draft_ids = []
        try:
            numeric_id = int(query_text.strip())
        except ValueError:
            numeric_id = None
        if numeric_id is not None:
            linked_draft_ids.extend(row[0] for row in session.execute(
                text("SELECT draft_id FROM draft_papers WHERE paper_id = :pid"), {"pid": numeric_id}
            ).fetchall())
            paper_ids = [row[0] for row in session.execute(
                text("SELECT id FROM papers WHERE internal_id = :iid"), {"iid": numeric_id}
            ).fetchall()]
            if paper_ids:
                linked_draft_ids.extend(row[0] for row in session.execute(
                    text("SELECT draft_id FROM draft_papers WHERE paper_id IN ({})".format(
                        ",".join(str(value) for value in paper_ids)
                    ))
                ).fetchall())
        if linked_draft_ids:
            query = query.filter(ArticleDraft.id.in_(set(linked_draft_ids)))
        else:
            query = query.filter(or_(
                ArticleDraft.title.ilike(f"%{query_text}%"),
                ArticleDraft.title_zh.ilike(f"%{query_text}%"),
                ArticleDraft.content.ilike(f"%{query_text}%"),
            ))
    total = query.count()
    drafts = query.order_by(ArticleDraft.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [draft_to_dict(draft, session) for draft in drafts],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_draft(session, draft_id: int, *, workspace: str, role: str = "admin") -> dict:
    draft = session.get(ArticleDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError
    if role == "viewer":
        for paper_id in get_paper_ids_for_draft(draft.id, session):
            paper = session.get(Paper, paper_id)
            if paper is not None and paper.review_outcome == "excluded":
                raise DraftNotFoundError(draft_id)
    return draft_to_dict(draft, session, include_paper=True, workspace=workspace)


def patch_draft(session, draft_id: int, changes: dict, *, reviewer: str, selected_images: list[str] | None) -> dict:
    draft = session.get(ArticleDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError(draft_id)
    next_status = changes.get("status")
    if next_status is not None:
        if next_status not in EDITABLE_STATUS_TRANSITIONS.get(draft.status, set()):
            raise InvalidDraftStatusTransition(f"Cannot change draft status from {draft.status} to {next_status}")
        draft.status = next_status
        if next_status in {"approved", "rejected"}:
            draft.reviewer = reviewer
            draft.reviewed_at = datetime.datetime.now(datetime.UTC)
    for field in ("title", "content", "review_notes"):
        if changes.get(field) is not None:
            setattr(draft, field, changes[field])
    if changes.get("meta") is not None:
        draft.meta = {**(draft.meta or {}), **changes["meta"]}
    if selected_images is not None:
        papers = get_papers_for_draft(draft.id, session)
        if papers:
            papers[0].selected_images = selected_images
    draft.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(draft)
    return {"ok": True, "id": draft_id}


def delete_draft(session, draft_id: int, *, workspace: str) -> bool:
    draft = session.get(ArticleDraft, draft_id)
    if draft is None:
        raise DraftNotFoundError(draft_id)
    draft_type = draft.draft_type
    for paper_id in get_paper_ids_for_draft(draft.id, session):
        if not draft_has_other_drafts_for_paper(draft.id, paper_id, session):
            paper = session.get(Paper, paper_id)
            if paper:
                paper.article_status = "not_generated"
    unlink_all_papers_from_draft(draft.id, session)
    for record in list(draft.publish_records):
        session.delete(record)
    session.flush()
    session.delete(draft)
    session.commit()
    if draft_type == "manual_upload":
        storage.safe_delete_dir(storage.get_draft_image_dir(draft_id, workspace))
        storage.safe_delete_dir(Path(workspace) / "data" / "cache" / "draft_images" / str(draft_id))
    return True
