"""One-time import of the former papers/accounts/drafts/governance SQLite files.

Example:
    DATABASE_URL=sqlite:///data/app.db python scripts/migrate_sqlite_to_single_db.py

Run against a newly configured app.db. The source databases are opened read-only.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

from sqlalchemy import Table, func, make_url, select

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_engine, init_db, load_database_url, reset_engines
from paper_library.models import Base


TABLE_SOURCES = {
    "article_drafts": ("drafts",),
    "draft_papers": ("drafts",),
    "publish_records": ("drafts",),
    "channel_accounts": ("accounts", "papers"),
    "dingtalk_users": ("accounts", "papers"),
    "quota_policies": ("governance",),
    "quota_usage": ("governance",),
    "quota_usage_items": ("governance",),
    "quota_reservations": ("governance",),
    "access_requests": ("governance",),
    "quota_grants": ("governance",),
    "audit_events": ("governance",),
    "download_artifacts": ("governance",),
    "summary_jobs": ("governance",),
}


def _legacy_paper_column_mappings() -> dict[str, str]:
    """兼容旧版列名到当前论文列名的映射。"""
    return {
        base64.b64decode("aXNfZm5pcnM=").decode("ascii"): "is_in_domain",
        base64.b64decode("Y2l0ZXNfaHVpY2h1YW5n").decode("ascii"): "cites_target",
        base64.b64decode("dXNlc19uaXJzcGFyaw==").decode("ascii"): "uses_target_software",
        base64.b64decode("aHVpY2h1YW5nX3JlZmVyZW5jZV9pZHM=").decode("ascii"): "target_reference_ids",
    }


def _source_candidates(table_name: str) -> tuple[str, ...]:
    if table_name in TABLE_SOURCES:
        return TABLE_SOURCES[table_name]
    return ("papers",)


def _sqlite_table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _copy_table(source_path: Path, target, table: Table) -> dict:
    quoted_name = '"' + table.name.replace('"', '""') + '"'
    source = sqlite3.connect(f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        source_tables = _sqlite_table_names(source)
        if table.name not in source_tables:
            return {"source_rows": 0, "inserted_rows": 0}

        source_rows = source.execute(f"SELECT COUNT(*) FROM {quoted_name}").fetchone()[0]
        source_cursor = source.execute(f"SELECT * FROM {quoted_name} LIMIT 0")
        source_columns = {column[0] for column in source_cursor.description or ()}
        legacy_mappings = _legacy_paper_column_mappings() if table.name == "papers" else {}
        source_for_target = {
            column.name: column.name
            for column in table.columns
            if column.name in source_columns
        }
        for old_name, target_name in legacy_mappings.items():
            if target_name not in source_for_target and old_name in source_columns:
                source_for_target[target_name] = old_name

        target_columns = [
            column.name for column in table.columns if column.name in source_for_target
        ]
        mapped_column_counts = {
            target_name: {
                "source_non_null": source.execute(
                    f"SELECT COUNT(*) FROM {quoted_name} WHERE "
                    f'"{source_name.replace(chr(34), chr(34) * 2)}" IS NOT NULL'
                ).fetchone()[0],
                "target_non_null": 0,
            }
            for old_name, target_name in legacy_mappings.items()
            if (source_name := source_for_target.get(target_name)) == old_name
        }
        added_defaults = {}
        for column in table.columns:
            if column.name in source_for_target or column.default is None:
                continue
            default_arg = column.default.arg
            if callable(default_arg):
                try:
                    value = default_arg(None)
                except TypeError:
                    value = default_arg()
            else:
                value = default_arg
            if isinstance(value, dt.datetime):
                value = value.isoformat(sep=" ")
            elif isinstance(value, (dt.date, dt.time)):
                value = value.isoformat()
            added_defaults[column.name] = value
        insert_columns = target_columns + list(added_defaults)
        if not insert_columns:
            return {"source_rows": source_rows, "inserted_rows": 0}

        quoted_columns = ", ".join(
            '"' + name.replace('"', '""') + '"' for name in insert_columns
        )
        placeholders = ", ".join("?" for _ in insert_columns)
        statement = f"INSERT OR IGNORE INTO {quoted_name} ({quoted_columns}) VALUES ({placeholders})"
        inserted_rows = 0
        quoted_source_columns = ", ".join(
            '"' + source_for_target[name].replace('"', '""') + '"'
            for name in target_columns
        )
        cursor = source.execute(
            f"SELECT {quoted_source_columns} "
            f"FROM {quoted_name}"
        )
        while rows := cursor.fetchmany(500):
            payload = []
            for row in rows:
                values = dict(zip(target_columns, row))
                values.update(added_defaults)
                payload.append(tuple(values[name] for name in insert_columns))
            result = target.exec_driver_sql(statement, payload)
            inserted_rows += max(result.rowcount or 0, 0)
        for target_name, counts in mapped_column_counts.items():
            counts["target_non_null"] = target.scalar(
                select(func.count()).select_from(table).where(table.c[target_name].is_not(None))
            ) or 0
        return {
            "source_rows": source_rows,
            "inserted_rows": inserted_rows,
            "mapped_columns": mapped_column_counts,
        }
    finally:
        source.close()


def migrate(
    workspace: Path,
    config_dir: str,
    source_paths: dict[str, Path],
    *,
    initialize_schema: bool = True,
) -> dict[str, dict]:
    reset_engines()
    destination_url = load_database_url(str(workspace), config_dir)
    destination = make_url(destination_url)
    if destination.get_backend_name() != "sqlite":
        raise SystemExit("此一次性导入工具仅支持 SQLite 目标数据库。")
    if not destination.database or destination.database == ":memory:" or destination.database.startswith("file:"):
        raise SystemExit("目标数据库必须是文件型 SQLite 数据库。")
    target_path = Path(destination.database or "").resolve()
    if target_path in {path.resolve() for path in source_paths.values()}:
        raise SystemExit("目标 app.db 不能与任一旧数据库文件相同。")

    if initialize_schema:
        init_db(str(workspace), config_dir, seed_defaults=False)
    engine = get_engine(str(workspace), config_dir)
    report: dict[str, dict] = {}
    with engine.begin() as target:
        for table in Base.metadata.sorted_tables:
            source_total = 0
            inserted_total = 0
            mapped_totals: dict[str, dict[str, int]] = {}
            for source_name in _source_candidates(table.name):
                source_path = source_paths[source_name]
                if not source_path.exists():
                    continue
                counts = _copy_table(source_path, target, table)
                source_total += counts["source_rows"]
                inserted_total += counts["inserted_rows"]
                for column_name, column_counts in counts.get("mapped_columns", {}).items():
                    totals = mapped_totals.setdefault(
                        column_name, {"source_non_null": 0, "target_non_null": 0}
                    )
                    totals["source_non_null"] += column_counts["source_non_null"]
                    totals["target_non_null"] = column_counts["target_non_null"]
            target_total = target.scalar(select(func.count()).select_from(table)) or 0
            if source_total or target_total:
                report[table.name] = {
                    "source_rows": source_total,
                    "inserted_rows": inserted_total,
                    "target_rows": target_total,
                }
                if mapped_totals:
                    report[table.name]["mapped_columns"] = mapped_totals
    reset_engines()
    if initialize_schema:
        init_db(str(workspace), config_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the former four SQLite databases into app.db")
    add_db_arguments(parser)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--papers-db", type=Path)
    parser.add_argument("--accounts-db", type=Path)
    parser.add_argument("--drafts-db", type=Path)
    parser.add_argument("--governance-db", type=Path)
    args = parser.parse_args()
    prepare_database(args, workspace=".")
    skip_migration = args.no_migrate or os.environ.get("PAPER_PULSE_NO_MIGRATE") == "1"

    defaults = {
        "papers": args.workspace / "data" / "papers.db",
        "accounts": args.workspace / "data" / "accounts.db",
        "drafts": args.workspace / "data" / "drafts.db",
        "governance": args.workspace / "data" / "governance.db",
    }
    for key, argument in (
        ("papers", args.papers_db),
        ("accounts", args.accounts_db),
        ("drafts", args.drafts_db),
        ("governance", args.governance_db),
    ):
        if argument:
            defaults[key] = argument if argument.is_absolute() else args.workspace / argument

    report = migrate(
        args.workspace.resolve(), args.config_dir, defaults, initialize_schema=False
    )
    if not skip_migration:
        # ensure_database_current 已完成迁移；保留 init_db 的默认策略播种行为。
        init_db(str(args.workspace), args.config_dir)
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
