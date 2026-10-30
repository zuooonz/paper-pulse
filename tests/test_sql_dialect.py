from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from paper_library.db import get_session_factory
from paper_library.json_utils import decode_json
from paper_library.models import Paper
from paper_library.sql_dialect import (
    json_array_contains,
    json_array_of_objects_field_equals,
    json_field_equals,
    group_concat_distinct,
    bool_any,
)


def _sql(statement, dialect):
    return str(statement.compile(dialect=dialect()))


def test_json_array_contains_compiles_for_both_supported_dialects():
    statement = select(Paper.id).where(json_array_contains(Paper.tags, "康复"))

    postgres_sql = _sql(statement, postgresql.dialect)
    sqlite_sql = _sql(statement, sqlite.dialect)

    assert "@> jsonb_build_array" in postgres_sql
    assert "json_each(papers.tags)" in sqlite_sql
    assert "json_each" not in postgres_sql


def test_group_concat_distinct_compiles_for_sqlite_and_postgresql():
    statement = select(group_concat_distinct(Paper.doi, ";"))

    postgres_sql = _sql(statement, postgresql.dialect)
    sqlite_sql = _sql(statement, sqlite.dialect)

    assert "string_agg(DISTINCT papers.doi, %(param_1)s::VARCHAR)" in postgres_sql
    assert "replace(group_concat(DISTINCT papers.doi), ',', ?)" in sqlite_sql


def test_bool_any_compiles_for_sqlite_and_postgresql():
    statement = select(bool_any(Paper.is_in_domain))

    postgres_sql = _sql(statement, postgresql.dialect)
    sqlite_sql = _sql(statement, sqlite.dialect)

    assert "BOOL_OR(papers.is_in_domain)" in postgres_sql
    assert "MAX(CAST(papers.is_in_domain AS INTEGER))" in sqlite_sql


def test_json_field_equals_compiles_for_boolean_fields():
    statement = select(Paper.id).where(
        json_field_equals(Paper.multimodal_techniques, "$.has_multimodal", True)
    )

    postgres_sql = _sql(statement, postgresql.dialect)
    sqlite_sql = _sql(statement, sqlite.dialect)

    assert "#>> '{has_multimodal}' = 'true'" in postgres_sql
    assert "json_extract(papers.multimodal_techniques, '$.has_multimodal')" in sqlite_sql


def test_json_array_object_fields_support_one_or_two_predicates():
    statement = select(Paper.id).where(json_array_of_objects_field_equals(
        Paper.multimodal_relations, "timing", "concurrent", "method", "EEG"
    ))

    postgres_sql = _sql(statement, postgresql.dialect)
    sqlite_sql = _sql(statement, sqlite.dialect)

    assert "jsonb_array_elements(CAST(papers.multimodal_relations AS JSONB))" in postgres_sql
    assert "json_item.value ->> 'timing'" in postgres_sql
    assert "json_item.value ->> 'method'" in postgres_sql
    assert "json_each(papers.multimodal_relations)" in sqlite_sql
    assert "json_extract(json_item.value, '$.timing')" in sqlite_sql
    assert "json_extract(json_item.value, '$.method')" in sqlite_sql


def test_decode_json_accepts_legacy_text_and_native_values():
    assert decode_json('["EEG"]') == ["EEG"]
    assert decode_json({"has_multimodal": True}) == {"has_multimodal": True}
    assert decode_json("not-json") == "not-json"


def test_brain_region_backfill_keeps_json_column_as_native_list():
    from scripts.enrich_brain_regions import _brain_regions_value

    assert _brain_regions_value(["frontal", "temporal"]) == ["frontal", "temporal"]
    assert _brain_regions_value(None) == []


def test_json_filter_expressions_match_native_json_values():
    session_factory = get_session_factory()
    with session_factory() as session:
        paper = Paper(
            title="JSON dialect check",
            tags=["康复"],
            multimodal_techniques={"has_multimodal": True},
            multimodal_relations=[{"timing": "concurrent", "method": "EEG"}],
        )
        session.add(paper)
        session.flush()
        paper_id = paper.id

        assert session.scalar(select(Paper.id).where(
            Paper.id == paper_id,
            json_array_contains(Paper.tags, "康复"),
        )) == paper_id
        assert session.scalar(select(Paper.id).where(
            Paper.id == paper_id,
            json_field_equals(Paper.multimodal_techniques, "$.has_multimodal", True),
        )) == paper_id
        assert session.scalar(select(Paper.id).where(
            Paper.id == paper_id,
            json_array_of_objects_field_equals(
                Paper.multimodal_relations, "timing", "concurrent", "method", "EEG"
            ),
        )) == paper_id
        session.rollback()
