"""Convert local HTML account records to the built-in publishing target.

Revision ID: 0010_local_builtin_channel
Revises: 0009_drop_wechat_channel
Create Date: 2026-10-22

Downgrade removes only the index. The data conversion is irreversible: local
accounts are deleted and publish records no longer retain their former account IDs.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_local_builtin_channel"
down_revision = "0009_drop_wechat_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE publish_records "
            "SET channel = 'local', account_id = NULL, account_name = '本地 HTML' "
            "WHERE account_id IN ("
            "  SELECT id FROM channel_accounts WHERE channel = 'local'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE publish_records SET idempotency_key = NULL "
            "WHERE channel = 'local' AND account_id IS NULL "
            "AND idempotency_key IS NOT NULL AND id NOT IN ("
            "  SELECT MIN(id) FROM publish_records "
            "  WHERE channel = 'local' AND account_id IS NULL "
            "    AND idempotency_key IS NOT NULL "
            "  GROUP BY draft_id, idempotency_key"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM channel_accounts WHERE channel = 'local'"))
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


def downgrade() -> None:
    op.drop_index("uq_publish_record_local_idempotency", table_name="publish_records")
