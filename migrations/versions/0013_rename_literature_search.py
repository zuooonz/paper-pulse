"""Rename monthly search jobs and paper source labels.

Revision ID: 0013_rename_literature_search
Revises: 0012_drop_unused_columns
"""

from __future__ import annotations

from datetime import date

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013_rename_literature_search"
down_revision = "0012_drop_unused_columns"
branch_labels = None
depends_on = None


JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

jobs = sa.table(
    "jobs",
    sa.column("id", sa.String(64)),
    sa.column("kind", sa.String(80)),
    sa.column("payload", JSON_TYPE),
    sa.column("result", JSON_TYPE),
)


def _date_value(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _convert_request(request, *, downgrade: bool) -> dict:
    if not isinstance(request, dict):
        return request

    converted = dict(request)
    if downgrade:
        mode = converted.get("mode", "recent")
        if mode == "range":
            date_from = _date_value(converted.pop("date_from"))
            date_to = _date_value(converted.pop("date_to"))
            converted["mode"] = "years"
            converted["year_from"] = date_from.year
            converted["year_to"] = date_to.year
            converted.pop("days", None)
            converted.pop("months", None)
        elif mode == "recent":
            days = int(converted.pop("days", 60))
            converted["months"] = max(1, min(24, (days + 15) // 30))
        else:
            converted.pop("days", None)
            converted.pop("date_from", None)
            converted.pop("date_to", None)
            converted.setdefault("months", 2)
    else:
        mode = converted.get("mode", "recent")
        if mode == "recent":
            months = int(converted.pop("months", 2))
            converted["days"] = months * 30
        else:
            converted.pop("months", None)
    return converted


def _convert_job_json(connection, *, downgrade: bool) -> None:
    old_kind, new_kind = (
        ("literature_search", "monthly_search")
        if downgrade
        else ("monthly_search", "literature_search")
    )
    rows = connection.execute(
        sa.select(jobs.c.id, jobs.c.payload, jobs.c.result).where(jobs.c.kind == old_kind)
    ).mappings().all()
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, dict):
            payload = dict(payload)
            if "request" in payload:
                payload["request"] = _convert_request(payload["request"], downgrade=downgrade)

        result = row["result"]
        if isinstance(result, dict):
            result = dict(result)
            if "request" in result:
                result["request"] = _convert_request(result["request"], downgrade=downgrade)

        connection.execute(
            sa.update(jobs)
            .where(jobs.c.id == row["id"])
            .values(kind=new_kind, payload=payload, result=result)
        )


def upgrade() -> None:
    connection = op.get_bind()
    _convert_job_json(connection, downgrade=False)
    connection.execute(
        sa.text(
            "UPDATE papers SET brand_source = 'literature_search' "
            "WHERE brand_source = 'monthly_search'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE papers SET source = 'literature_search' "
            "WHERE source = 'monthly_search'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE papers SET source = replace(source, '_monthly_search', '_literature_search') "
            "WHERE source LIKE '%monthly_search'"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    _convert_job_json(connection, downgrade=True)
    connection.execute(
        sa.text(
            "UPDATE papers SET brand_source = 'monthly_search' "
            "WHERE brand_source = 'literature_search'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE papers SET source = 'monthly_search' "
            "WHERE source = 'literature_search'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE papers SET source = replace(source, '_literature_search', '_monthly_search') "
            "WHERE source LIKE '%literature_search'"
        )
    )
