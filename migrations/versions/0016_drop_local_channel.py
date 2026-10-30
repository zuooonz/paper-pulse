"""Remove the local HTML publishing target.

Revision ID: 0016_drop_local_channel
Revises: 0015_paper_lifecycle
Create Date: 2026-10-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0016_drop_local_channel"
down_revision = "0015_paper_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "UPDATE publish_records "
        "SET status = 'cancelled', error = '本地 HTML 渠道已移除' "
        "WHERE channel = 'local' AND status IN ('pending', 'scheduled', 'publishing')"
    ))
    op.drop_index("uq_publish_record_local_idempotency", table_name="publish_records")


def downgrade() -> None:
    op.create_index(
        "uq_publish_record_local_idempotency",
        "publish_records",
        ["draft_id", "idempotency_key"],
        unique=True,
        sqlite_where=sa.text(
            "channel = 'local' AND account_id IS NULL AND idempotency_key IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "channel = 'local' AND account_id IS NULL AND idempotency_key IS NOT NULL"
        ),
    )
