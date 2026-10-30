"""Align review outcomes with completed processing.

A review decision is only meaningful after the processing pipeline finished.
Older code allowed approving or excluding pending/failed papers, which produced
states such as ``failed + approved``.  Reset those stale decisions so the
invariant "non-done papers are unreviewed" holds for existing data too.

Revision ID: 0017_align_review_outcome
Revises: 0016_drop_local_channel
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017_align_review_outcome"
down_revision = "0016_drop_local_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE papers "
        "SET review_outcome = 'unreviewed', "
        "reviewed_by = NULL, reviewed_at = NULL, review_note = NULL "
        "WHERE processing_status != 'done' AND review_outcome != 'unreviewed'"
    ))


def downgrade() -> None:
    # The previous review decisions cannot be reconstructed; downgrade only
    # restores the schema, not the discarded review metadata.
    pass
