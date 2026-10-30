"""Centralized paper processing, review, PDF, and generation lifecycle rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.domain_config import generation_requires_target_entity
from paper_library.models import Paper
from paper_library.time_utils import utc_now
from tools.storage import resolve_storage_path


PROCESSING_STATUSES = frozenset({"pending", "processing", "done", "failed"})
REVIEW_OUTCOMES = frozenset({"unreviewed", "approved", "excluded"})


def compute_metadata_complete(paper: Any) -> bool:
    """Return whether all required bibliographic metadata is present."""
    authors = getattr(paper, "authors", None) or []
    return bool(
        str(getattr(paper, "title", "") or "").strip()
        and (
            str(getattr(paper, "doi", "") or "").strip()
            or str(getattr(paper, "pmid", "") or "").strip()
        )
        and getattr(paper, "year", None) is not None
        and str(getattr(paper, "journal_name", "") or "").strip()
        and str(getattr(paper, "abstract", "") or "").strip()
        and len(authors) > 0
    )


def refresh_metadata_complete(paper: Any) -> bool:
    paper.metadata_complete = compute_metadata_complete(paper)
    return paper.metadata_complete


def refresh_pdf_flag(paper: Any, workspace: str | Path) -> bool:
    """Set has_pdf from the resolved file's actual existence."""
    resolved = resolve_storage_path(getattr(paper, "pdf_storage_path", None), workspace)
    paper.has_pdf = bool(resolved and resolved.is_file())
    return paper.has_pdf


def reconcile_pdf_flags(session: Any, workspace: str | Path) -> int:
    """Refresh stale PDF availability flags in bounded batches."""
    batch_size = 500
    changed = 0
    batch_changes = 0
    papers = session.query(Paper).order_by(Paper.id).yield_per(batch_size)

    for index, paper in enumerate(papers, start=1):
        previous = paper.has_pdf
        if refresh_pdf_flag(paper, workspace) != previous:
            changed += 1
            batch_changes += 1
        if index % batch_size == 0 and batch_changes:
            session.flush()
            batch_changes = 0

    if batch_changes:
        session.flush()
    return changed


def start_processing(paper: Any) -> None:
    """Start a new processing run.

    Reprocessing can change the extracted content, so any previous human
    decision is no longer valid.  Reset it here to keep the invariant that a
    review outcome only exists for a completed paper.
    """
    if getattr(paper, "review_outcome", "unreviewed") != "unreviewed":
        reset_review(paper)
    paper.processing_status = "processing"
    paper.processing_error = None


def reset_processing_to_pending(paper: Any) -> None:
    if getattr(paper, "review_outcome", "unreviewed") != "unreviewed":
        reset_review(paper)
    paper.processing_status = "pending"
    paper.processing_error = None


def complete_processing(paper: Any) -> None:
    paper.processing_status = "done"
    paper.processing_error = None
    refresh_metadata_complete(paper)


def fail_processing(paper: Any, error: str) -> None:
    if getattr(paper, "review_outcome", "unreviewed") != "unreviewed":
        reset_review(paper)
    paper.processing_status = "failed"
    paper.processing_error = str(error or "processing_failed")


def can_review_paper(paper: Any) -> bool:
    """A paper can be reviewed only after its processing pipeline finished."""
    return getattr(paper, "processing_status", None) == "done"


def record_download_error(paper: Any, error: str = "pdf_download_failed") -> None:
    """Record a download issue without changing processing or review status."""
    paper.processing_error = str(error or "pdf_download_failed")


def approve_paper(paper: Any, reviewer: str, note: str | None = None) -> None:
    paper.review_outcome = "approved"
    paper.reviewed_by = reviewer
    paper.reviewed_at = utc_now()
    paper.review_note = (note or "").strip() or None


def exclude_paper(paper: Any, reviewer: str, note: str | None = None) -> None:
    paper.review_outcome = "excluded"
    paper.reviewed_by = reviewer
    paper.reviewed_at = utc_now()
    paper.review_note = (note or "").strip() or None


def reset_review(paper: Any) -> None:
    paper.review_outcome = "unreviewed"
    paper.reviewed_by = None
    paper.reviewed_at = None
    paper.review_note = None


def is_usable_for_generation(
    paper: Any,
    require_target_entity: bool | None,
    target_label: str | None,
) -> bool:
    """Apply the shared processing, human review, and entity-scope gate."""
    if getattr(paper, "processing_status", None) != "done":
        return False
    if getattr(paper, "review_outcome", None) != "approved":
        return False
    if require_target_entity is None:
        require_target_entity = generation_requires_target_entity()
    if not require_target_entity:
        return True
    return bool(target_label and getattr(paper, "entity_label", None) == target_label)


