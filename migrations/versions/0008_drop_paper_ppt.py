"""Remove paper presentation attachment metadata.

Revision ID: 0008_drop_paper_ppt
Revises: 0007_clear_legacy_taxonomy
Create Date: 2026-10-22

Files on disk are intentionally left untouched.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_drop_paper_ppt"
down_revision = "0007_clear_legacy_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.drop_column("ppt_storage_path")


def downgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.add_column(sa.Column("ppt_storage_path", sa.String(length=1000), nullable=True))
