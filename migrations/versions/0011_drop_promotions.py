"""Drop historical promotion records and paper links.

Revision ID: 0011_drop_promotions
Revises: 0010_local_builtin_channel
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_drop_promotions"
down_revision = "0010_local_builtin_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("paper_promotion_links")
    op.drop_table("promotion_records")


def downgrade() -> None:
    op.create_table(
        "promotion_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("promotion_type", sa.String(length=30), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("match_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_file"),
    )
    op.create_index("ix_promotion_records_channel", "promotion_records", ["channel"])
    op.create_index("ix_promotion_records_content_hash", "promotion_records", ["content_hash"])
    op.create_index("ix_promotion_records_match_status", "promotion_records", ["match_status"])
    op.create_index("ix_promotion_records_promotion_type", "promotion_records", ["promotion_type"])
    op.create_index("ix_promotion_records_published_at", "promotion_records", ["published_at"])

    op.create_table(
        "paper_promotion_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("paper_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=False),
        sa.Column("match_method", sa.String(length=30), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"]),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id", "promotion_id", name="uq_paper_promotion_link"),
    )
    op.create_index("ix_paper_promotion_links_paper_id", "paper_promotion_links", ["paper_id"])
    op.create_index("ix_paper_promotion_links_promotion_id", "paper_promotion_links", ["promotion_id"])
    op.create_index(
        "ix_paper_promotion_links_verification_status",
        "paper_promotion_links",
        ["verification_status"],
    )
