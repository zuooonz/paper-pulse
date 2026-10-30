"""Use PostgreSQL JSONB and timezone-aware timestamps.

Revision ID: 0002_json_columns
Revises: 0001_baseline
"""

from __future__ import annotations

from alembic import op


revision = "0002_json_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


JSON_COLUMNS = {
    "affiliation_geo_evidence": ("raw_response",),
    "article_drafts": ("meta",),
    "papers": (
        "doi_meta", "target_reference_ids", "extracted_images", "selected_images",
        "keywords", "keywords_zh", "research_fields", "population", "paradigm",
        "equipment_brand", "tags", "highlights_zh", "metadata_confidence",
        "brain_regions", "multimodal_techniques", "subject_diseases", "research_topics",
        "neuroscience_subtopics", "paradigms", "multimodal_methods", "multimodal_relations",
        "classification_sources", "original_data",
    ),
}

# These columns were created as timestamp without time zone in 0001. Values were
# written from UTC-aware application timestamps, so the stored wall time is UTC.
DATETIME_COLUMNS = {
    "affiliation_geo_evidence": ("checked_at",),
    "article_drafts": ("reviewed_at", "published_at", "created_at", "updated_at"),
    "author_identity_events": ("created_at",),
    "authors": ("created_at", "updated_at"),
    "channel_accounts": ("expires_at", "bound_at", "created_at", "updated_at", "last_validated_at"),
    "channel_credentials": ("expires_at", "created_at", "updated_at"),
    "channel_videos": ("created_at", "updated_at"),
    "dingtalk_users": ("created_at", "updated_at"),
    "journals": ("created_at", "updated_at"),
    "promotion_records": ("published_at", "created_at", "updated_at"),
    "journal_metrics": ("created_at", "updated_at"),
    "papers": ("created_at", "updated_at"),
    "publish_records": ("scheduled_at", "published_at", "created_at"),
    "video_publish_records": ("scheduled_at", "published_at", "created_at"),
    "author_identifiers": ("created_at", "verified_at"),
    "paper_author_mentions": ("verified_at", "created_at", "updated_at"),
    "paper_promotion_links": ("created_at",),
}


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table, columns in JSON_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE JSONB USING "{column}"::jsonb'
            )

    for table, columns in DATETIME_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE TIMESTAMP WITH TIME ZONE USING "{column}" AT TIME ZONE \'UTC\''
            )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table, columns in DATETIME_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE TIMESTAMP WITHOUT TIME ZONE USING "{column}" AT TIME ZONE \'UTC\''
            )

    for table, columns in JSON_COLUMNS.items():
        for column in columns:
            op.execute(
                f'ALTER TABLE "{table}" ALTER COLUMN "{column}" '
                f'TYPE JSON USING "{column}"::json'
            )
