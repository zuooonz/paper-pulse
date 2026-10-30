"""Track canonical affiliation locations in the application schema.

Revision ID: 0004_affiliation_locations
Revises: 0003_durable_jobs
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_affiliation_locations"
down_revision = "0003_durable_jobs"
branch_labels = None
depends_on = None


_AUTHOR_LOCATION_FK = "fk_authors_canonical_affiliation_id_affiliation_locations"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "affiliation_locations" not in inspector.get_table_names():
        op.create_table(
            "affiliation_locations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("affiliation_zh", sa.Text(), nullable=False, unique=True),
            sa.Column("canonical_name_en", sa.Text(), nullable=True),
            sa.Column("city", sa.Text(), nullable=True),
            sa.Column("province", sa.Text(), nullable=True),
            sa.Column("country", sa.Text(), nullable=True),
            sa.Column("lat", sa.Float(), nullable=True),
            sa.Column("lng", sa.Float(), nullable=True),
            sa.Column("geocoded_at", sa.DateTime(timezone=True), nullable=True),
        )
    else:
        # Older installations may already have this table from the original
        # standalone geocoding script. Adopt it and add fields introduced later.
        existing_columns = {
            column["name"]
            for column in sa.inspect(bind).get_columns("affiliation_locations")
        }
        missing_columns = {
            "canonical_name_en": sa.Column("canonical_name_en", sa.Text(), nullable=True),
            "country": sa.Column("country", sa.Text(), nullable=True),
        }
        missing_columns = {
            name: column for name, column in missing_columns.items()
            if name not in existing_columns
        }
        if missing_columns:
            with op.batch_alter_table("affiliation_locations") as batch_op:
                for column in missing_columns.values():
                    batch_op.add_column(column)

    author_foreign_keys = sa.inspect(bind).get_foreign_keys("authors")
    has_location_fk = any(
        foreign_key.get("constrained_columns") == ["canonical_affiliation_id"]
        and foreign_key.get("referred_table") == "affiliation_locations"
        for foreign_key in author_foreign_keys
    )
    if not has_location_fk:
        # Clear only dangling links so adding the constraint also works on
        # databases where the old standalone table was removed or incomplete.
        op.execute(sa.text("""
            UPDATE authors
            SET canonical_affiliation_id = NULL
            WHERE canonical_affiliation_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM affiliation_locations
                  WHERE affiliation_locations.id = authors.canonical_affiliation_id
              )
        """))
        with op.batch_alter_table("authors") as batch_op:
            batch_op.create_foreign_key(
                _AUTHOR_LOCATION_FK,
                "affiliation_locations",
                ["canonical_affiliation_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "authors" in inspector.get_table_names():
        if any(
            foreign_key.get("name") == _AUTHOR_LOCATION_FK
            for foreign_key in inspector.get_foreign_keys("authors")
        ):
            with op.batch_alter_table("authors") as batch_op:
                batch_op.drop_constraint(_AUTHOR_LOCATION_FK, type_="foreignkey")
    if "affiliation_locations" in inspector.get_table_names():
        op.drop_table("affiliation_locations")
