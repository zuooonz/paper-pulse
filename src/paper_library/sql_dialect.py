"""SQL expressions for JSON operations supported by SQLite and PostgreSQL."""

from __future__ import annotations

import re

from sqlalchemy import Boolean, String, bindparam
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement


_JSON_PATH = re.compile(r"^\$((\.[A-Za-z_][A-Za-z0-9_]*)|(\[\d+\]))*$")
_JSON_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_field(field: str) -> str:
    if not _JSON_FIELD.fullmatch(field):
        raise ValueError("JSON object fields must be simple identifiers")
    return field


def _path_fields(path: str) -> list[str]:
    if not _JSON_PATH.fullmatch(path):
        raise ValueError("JSON path must use simple object keys and array indices")
    return [
        key or index
        for key, index in re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path)
    ]


def _sql_string(compiler, value: str) -> str:
    return compiler.render_literal_value(value, String())


def _jsonb_column(compiler, column, **kw) -> str:
    return f"CAST({compiler.process(column, **kw)} AS JSONB)"


class _JSONArrayContains(FunctionElement):
    type = Boolean()
    inherit_cache = False

    def __init__(self, column, value):
        super().__init__(column, bindparam(None, value))


class _JSONFieldEquals(FunctionElement):
    type = Boolean()
    inherit_cache = False

    def __init__(self, column, path: str, value):
        self.path = path
        _path_fields(path)
        super().__init__(column, bindparam(None, value))


class _JSONArrayObjectFieldEquals(FunctionElement):
    type = Boolean()
    inherit_cache = False

    def __init__(self, column, field: str, value, extra_field: str | None = None, extra_value=None):
        self.field = _validate_field(field)
        self.extra_field = _validate_field(extra_field) if extra_field is not None else None
        self.has_extra = extra_field is not None
        clauses = [column, bindparam(None, value)]
        if self.has_extra:
            clauses.append(bindparam(None, extra_value))
        super().__init__(*clauses)


class _GroupConcatDistinct(FunctionElement):
    type = String()
    inherit_cache = False

    def __init__(self, expression, separator: str):
        super().__init__(expression, bindparam(None, separator))


class _BoolAny(FunctionElement):
    type = Boolean()
    inherit_cache = False

    def __init__(self, expression):
        super().__init__(expression)


def group_concat_distinct(expression, sep: str = ","):
    """Concatenate distinct non-null values with a dialect-aware separator."""
    return _GroupConcatDistinct(expression, sep)


def bool_any(expression):
    """Return whether any non-null boolean expression in a group is true."""
    return _BoolAny(expression)


def json_array_contains(column, value):
    """Return true when a JSON array contains the given scalar value."""
    return _JSONArrayContains(column, value)


def json_field_equals(column, path: str, value):
    """Return true when a JSON object field at ``path`` equals ``value``."""
    return _JSONFieldEquals(column, path, value)


def json_array_of_objects_field_equals(
    column,
    field: str,
    value,
    extra_field: str | None = None,
    extra_value=None,
):
    """Match a field on any object in a JSON array, optionally with a second field."""
    return _JSONArrayObjectFieldEquals(column, field, value, extra_field, extra_value)


@compiles(_JSONArrayContains, "sqlite")
def _compile_array_contains_sqlite(element, compiler, **kw):
    column, value = list(element.clauses)
    return (
        "EXISTS (SELECT 1 FROM json_each("
        f"{compiler.process(column, **kw)}) WHERE value = {compiler.process(value, **kw)})"
    )


@compiles(_JSONArrayContains, "postgresql")
def _compile_array_contains_postgresql(element, compiler, **kw):
    column, value = list(element.clauses)
    return (
        f"{_jsonb_column(compiler, column, **kw)} @> "
        f"jsonb_build_array({compiler.process(value, **kw)})"
    )


@compiles(_JSONFieldEquals, "sqlite")
def _compile_field_equals_sqlite(element, compiler, **kw):
    column, value = list(element.clauses)
    path = _sql_string(compiler, element.path)
    return f"json_extract({compiler.process(column, **kw)}, {path}) = {compiler.process(value, **kw)}"


@compiles(_JSONFieldEquals, "postgresql")
def _compile_field_equals_postgresql(element, compiler, **kw):
    column, value = list(element.clauses)
    fields = _path_fields(element.path)
    path = "{" + ",".join(fields) + "}"
    if isinstance(value.value, bool):
        rhs = "'true'" if value.value else "'false'"
    else:
        rhs = f"CAST({compiler.process(value, **kw)} AS TEXT)"
    return f"{_jsonb_column(compiler, column, **kw)} #>> {_sql_string(compiler, path)} = {rhs}"


@compiles(_JSONArrayObjectFieldEquals, "sqlite")
def _compile_object_array_equals_sqlite(element, compiler, **kw):
    column, *values = list(element.clauses)
    predicates = [
        f"json_extract(json_item.value, {_sql_string(compiler, '$.' + element.field)}) = "
        f"{compiler.process(values[0], **kw)}"
    ]
    if element.has_extra:
        predicates.append(
            f"json_extract(json_item.value, {_sql_string(compiler, '$.' + element.extra_field)}) = "
            f"{compiler.process(values[1], **kw)}"
        )
    return (
        "EXISTS (SELECT 1 FROM json_each("
        f"{compiler.process(column, **kw)}) AS json_item WHERE {' AND '.join(predicates)})"
    )


@compiles(_JSONArrayObjectFieldEquals, "postgresql")
def _compile_object_array_equals_postgresql(element, compiler, **kw):
    column, *values = list(element.clauses)
    predicates = [
        f"json_item.value ->> {_sql_string(compiler, element.field)} = "
        f"CAST({compiler.process(values[0], **kw)} AS TEXT)"
    ]
    if element.has_extra:
        predicates.append(
            f"json_item.value ->> {_sql_string(compiler, element.extra_field)} = "
            f"CAST({compiler.process(values[1], **kw)} AS TEXT)"
        )
    return (
        "EXISTS (SELECT 1 FROM jsonb_array_elements("
        f"{_jsonb_column(compiler, column, **kw)}) AS json_item(value) "
        f"WHERE {' AND '.join(predicates)})"
    )


@compiles(_GroupConcatDistinct, "sqlite")
def _compile_group_concat_distinct_sqlite(element, compiler, **kw):
    expression, separator = list(element.clauses)
    compiled_expression = compiler.process(expression, **kw)
    compiled_separator = compiler.process(separator, **kw)
    if separator.value == ",":
        # SQLite permits DISTINCT only with its default single-argument form.
        return f"group_concat(DISTINCT {compiled_expression})"
    return f"replace(group_concat(DISTINCT {compiled_expression}), ',', {compiled_separator})"


@compiles(_GroupConcatDistinct, "postgresql")
def _compile_group_concat_distinct_postgresql(element, compiler, **kw):
    expression, separator = list(element.clauses)
    return (
        "string_agg(DISTINCT "
        f"{compiler.process(expression, **kw)}, {compiler.process(separator, **kw)})"
    )


@compiles(_GroupConcatDistinct)
def _compile_group_concat_distinct_default(element, compiler, **kw):
    expression, separator = list(element.clauses)
    return (
        "string_agg(DISTINCT "
        f"{compiler.process(expression, **kw)}, {compiler.process(separator, **kw)})"
    )


@compiles(_BoolAny, "sqlite")
def _compile_bool_any_sqlite(element, compiler, **kw):
    expression = next(iter(element.clauses))
    return f"MAX(CAST({compiler.process(expression, **kw)} AS INTEGER))"


@compiles(_BoolAny, "postgresql")
def _compile_bool_any_postgresql(element, compiler, **kw):
    expression = next(iter(element.clauses))
    return f"BOOL_OR({compiler.process(expression, **kw)})"


@compiles(_BoolAny)
def _compile_bool_any_default(element, compiler, **kw):
    expression = next(iter(element.clauses))
    return f"BOOL_OR({compiler.process(expression, **kw)})"
