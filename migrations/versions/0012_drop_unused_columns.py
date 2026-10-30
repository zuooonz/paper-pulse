"""Drop unused draft and publish columns and the legacy credential table.

Revision ID: 0012_drop_unused_columns
Revises: 0011_drop_promotions
Create Date: 2026-10-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_drop_unused_columns"
down_revision = "0011_drop_promotions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("article_drafts") as batch_op:
        batch_op.drop_column("audio_content")
        batch_op.drop_column("video_script")
        batch_op.drop_column("published_url")

    with op.batch_alter_table("publish_records") as batch_op:
        batch_op.drop_column("retry_count")

    op.drop_table("channel_credentials")


def downgrade() -> None:
    op.create_table(
        "channel_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("credential_type", sa.String(length=20), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_channel_credentials_channel",
        "channel_credentials",
        ["channel"],
        unique=True,
    )

    with op.batch_alter_table("publish_records") as batch_op:
        batch_op.add_column(
            sa.Column("retry_count", sa.Integer(), nullable=True, server_default=sa.text("0"))
        )

    with op.batch_alter_table("article_drafts") as batch_op:
        batch_op.add_column(sa.Column("audio_content", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("video_script", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("published_url", sa.String(length=1000), nullable=True))
