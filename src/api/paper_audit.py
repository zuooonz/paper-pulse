"""Stable paper identity snapshots for governance audit events."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select

from paper_library.db import get_session
from paper_library.models import Paper

from .config import settings


def paper_identity_snapshot(paper_ids: Iterable[int]) -> dict[str, list[int]]:
    """Return ordered DB IDs and their current Cite IDs for audit storage."""
    normalized_ids = list(dict.fromkeys(int(value) for value in paper_ids))
    if not normalized_ids:
        return {"paper_ids": [], "paper_internal_ids": []}

    try:
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
            internal_by_id = dict(session.execute(
                select(Paper.id, Paper.internal_id).where(
                    Paper.id.in_(normalized_ids),
                    Paper.internal_id.is_not(None),
                )
            ).all())
    except Exception:
        # Audit recording must not make the underlying business operation fail.
        internal_by_id = {}

    return {
        "paper_ids": normalized_ids,
        "paper_internal_ids": [
            int(internal_by_id[paper_id])
            for paper_id in normalized_ids
            if paper_id in internal_by_id
        ],
    }
