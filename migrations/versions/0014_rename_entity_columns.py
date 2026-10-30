"""Rename paper entity fields and the limited-user role."""

from alembic import op
import sqlalchemy as sa


revision = "0014_rename_entity_columns"
down_revision = "0013_rename_literature_search"
branch_labels = None
depends_on = None


def _replace_stored_role(old_role: str, new_role: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    role_columns = {
        "quota_policies": "role",
        "access_requests": "requester_role",
        "audit_events": "role",
        "dingtalk_users": "role",
    }

    if "quota_policies" in tables:
        # A deployment may already contain a manually-created limited policy.
        # Keep that row when the legacy policy has the same scope to avoid a
        # unique-key conflict; otherwise preserve the legacy policy by renaming it.
        bind.execute(
            sa.text(
                "DELETE FROM quota_policies AS legacy "
                "WHERE legacy.role = :old_role AND EXISTS ("
                "SELECT 1 FROM quota_policies AS current "
                "WHERE current.role = :new_role "
                "AND current.resource_type = legacy.resource_type "
                "AND current.period_type = legacy.period_type)"
            ),
            {"old_role": old_role, "new_role": new_role},
        )

    for table, column in role_columns.items():
        if table not in tables:
            continue
        columns = {item["name"] for item in inspector.get_columns(table)}
        if column not in columns:
            continue
        bind.execute(
            sa.text(f"UPDATE {table} SET {column} = :new_role WHERE {column} = :old_role"),
            {"old_role": old_role, "new_role": new_role},
        )


def _rename_paper_entity_columns(old_names: tuple[str, str, str], new_names: tuple[str, str, str]) -> None:
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("papers")}
    old_index = f"ix_papers_{old_names[0]}"
    new_index = f"ix_papers_{new_names[0]}"
    if old_index in indexes:
        op.drop_index(old_index, table_name="papers")

    with op.batch_alter_table("papers") as batch_op:
        for old_name, new_name, column_type in zip(
            old_names,
            new_names,
            (sa.String(length=100), sa.String(length=20), sa.String(length=10)),
            strict=True,
        ):
            batch_op.alter_column(
                old_name,
                new_column_name=new_name,
                existing_type=column_type,
                existing_nullable=True,
            )

    if new_index not in indexes:
        op.create_index(new_index, "papers", [new_names[0]], unique=False)


def upgrade() -> None:
    _rename_paper_entity_columns(
        ("brand", "brand_source", "brand_confidence"),
        ("entity_label", "entity_source", "entity_confidence"),
    )
    _replace_stored_role("sales", "limited")


def downgrade() -> None:
    _replace_stored_role("limited", "sales")
    _rename_paper_entity_columns(
        ("entity_label", "entity_source", "entity_confidence"),
        ("brand", "brand_source", "brand_confidence"),
    )
