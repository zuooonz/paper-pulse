"""Remove the WeChat publishing channel.

Revision ID: 0009_drop_wechat_channel
Revises: 0008_drop_paper_ppt
Create Date: 2026-10-22

Downgrade restores the nullable draft column, but deleted accounts and their
credentials, detached account references, and dropped draft content cannot be recovered.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_drop_wechat_channel"
down_revision = "0008_drop_paper_ppt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE publish_records "
            "SET status = 'cancelled', error = '渠道已移除' "
            "WHERE account_id IN ("
            "  SELECT id FROM channel_accounts WHERE channel = 'wechat'"
            ") AND status IN ('pending', 'scheduled', 'publishing')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE publish_records SET account_id = NULL "
            "WHERE account_id IN ("
            "  SELECT id FROM channel_accounts WHERE channel = 'wechat'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM channel_accounts WHERE channel = 'wechat'"))

    with op.batch_alter_table("article_drafts") as batch_op:
        batch_op.drop_column("content_wechat")


def downgrade() -> None:
    with op.batch_alter_table("article_drafts") as batch_op:
        batch_op.add_column(sa.Column("content_wechat", sa.Text(), nullable=True))
