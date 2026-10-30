"""Add idempotency keys and publication update timestamps.

Revision ID: 0006_publish_idempotency
Revises: 0005_author_merge_log
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_publish_idempotency"
down_revision = "0005_author_merge_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_records", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("publish_records", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE publish_records SET updated_at = created_at WHERE updated_at IS NULL")
    with op.batch_alter_table("publish_records") as batch_op:
        batch_op.create_unique_constraint(
            "uq_publish_record_draft_account_idempotency_key",
            ["draft_id", "account_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("publish_records") as batch_op:
        batch_op.drop_constraint(
            "uq_publish_record_draft_account_idempotency_key",
            type_="unique",
        )
        batch_op.drop_column("updated_at")
        batch_op.drop_column("idempotency_key")
