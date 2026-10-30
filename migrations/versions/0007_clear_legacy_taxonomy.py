"""Keep the database free of application-wide taxonomy seed defaults.

Revision ID: 0007_clear_legacy_taxonomy
Revises: 0006_publish_idempotency
Create Date: 2026-10-22

Taxonomy options are configuration-only in this project; there is no database
seed table for them. Paper classifications are user data and are cleaned by
the explicit dry-run script, so this structure-independent migration is a
no-op unless a future schema introduces a dedicated seed table.
"""

from __future__ import annotations


revision = "0007_clear_legacy_taxonomy"
down_revision = "0006_publish_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No taxonomy seed/default rows exist in the current schema.
    pass


def downgrade() -> None:
    # There was no data to remove during upgrade.
    pass
