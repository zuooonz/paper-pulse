import sqlite3

from sqlalchemy import select

from paper_library import db
from paper_library.governance_models import QuotaPolicy
from paper_library.models import ArticleDraft, ChannelAccount, Paper, PublishRecord, draft_papers
from scripts.migrate_sqlite_to_single_db import _legacy_paper_column_mappings, migrate


def _create_legacy_database(path, statements):
    connection = sqlite3.connect(path)
    try:
        for statement in statements:
            if isinstance(statement, tuple):
                connection.execute(*statement)
            else:
                connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def test_legacy_sqlite_files_merge_in_foreign_key_order(tmp_path, monkeypatch):
    workspace = tmp_path / "legacy-workspace"
    data = workspace / "data"
    data.mkdir(parents=True)
    sources = {name: data / f"{name}.db" for name in ("papers", "accounts", "drafts", "governance")}
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data / 'app.db'}")
    monkeypatch.delenv("DB_INIT_MODE", raising=False)
    db.reset_engines()

    _create_legacy_database(sources["papers"], [
        "CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL)",
        "INSERT INTO papers VALUES (41, 'Imported paper')",
    ])
    _create_legacy_database(sources["accounts"], [
        "CREATE TABLE channel_accounts (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, account_name TEXT NOT NULL)",
        "INSERT INTO channel_accounts VALUES (7, 'example', 'Example account')",
    ])
    _create_legacy_database(sources["drafts"], [
        "CREATE TABLE article_drafts (id INTEGER PRIMARY KEY, draft_type TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL)",
        "INSERT INTO article_drafts VALUES (3, 'review', 'Imported draft', 'Draft body', 'draft')",
        "CREATE TABLE draft_papers (draft_id INTEGER NOT NULL, paper_id INTEGER NOT NULL, PRIMARY KEY (draft_id, paper_id))",
        "INSERT INTO draft_papers VALUES (3, 41)",
        "CREATE TABLE publish_records (id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL, account_id INTEGER)",
        "INSERT INTO publish_records VALUES (11, 3, 7)",
    ])
    _create_legacy_database(sources["governance"], [
        "CREATE TABLE quota_policies (id INTEGER PRIMARY KEY, role TEXT NOT NULL, resource_type TEXT NOT NULL, period_type TEXT NOT NULL, limit_value INTEGER NOT NULL, timezone TEXT NOT NULL, enabled BOOLEAN NOT NULL, created_at_utc DATETIME NOT NULL, updated_at_utc DATETIME NOT NULL, UNIQUE (role, resource_type, period_type))",
        "INSERT INTO quota_policies VALUES (9, 'limited', 'paper', 'day', 250, 'Asia/Shanghai', 1, '2026-01-01', '2026-01-01')",
    ])

    report = migrate(workspace, "config", sources)
    db.get_engine(str(workspace), "config")
    with db.get_session(str(workspace), "config") as session:
        assert session.get(Paper, 41).title == "Imported paper"
        assert session.get(ArticleDraft, 3).title == "Imported draft"
        assert session.get(ChannelAccount, 7).account_name == "Example account"
        assert session.get(PublishRecord, 11).account_id == 7
        assert session.execute(select(draft_papers.c.paper_id).where(
            draft_papers.c.draft_id == 3
        )).scalar_one() == 41
        policy = session.query(QuotaPolicy).filter_by(role="limited").one()
        assert policy.limit_value == 250
        assert policy.enabled is True

    assert report["papers"] == {"source_rows": 1, "inserted_rows": 1, "target_rows": 1}
    assert report["publish_records"]["target_rows"] == 1
    assert report["quota_policies"] == {"source_rows": 1, "inserted_rows": 1, "target_rows": 1}
    db.reset_engines()


def test_legacy_paper_columns_are_mapped_and_reported(tmp_path, monkeypatch):
    workspace = tmp_path / "mapped-workspace"
    data = workspace / "data"
    data.mkdir(parents=True)
    sources = {name: data / f"{name}.db" for name in ("papers", "accounts", "drafts", "governance")}
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{data / 'app.db'}")
    monkeypatch.delenv("DB_INIT_MODE", raising=False)
    db.reset_engines()

    mappings = _legacy_paper_column_mappings()
    old_columns = list(mappings)
    quoted_columns = ", ".join(f'"{column}"' for column in old_columns)
    legacy_values = {
        target: ("[41,42]" if target == "target_reference_ids" else 1)
        for target in mappings.values()
    }
    values = [1, "Mapped paper", *(legacy_values[mappings[column]] for column in old_columns)]
    placeholders = ", ".join("?" for _ in values)
    _create_legacy_database(sources["papers"], [
        f'CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT NOT NULL, {quoted_columns})',
        (
            f'INSERT INTO papers (id, title, {quoted_columns}) VALUES ({placeholders})',
            values,
        ),
    ])

    report = migrate(workspace, "config", sources)
    with db.get_session(str(workspace), "config") as session:
        paper = session.get(Paper, 1)
        assert paper.is_in_domain is True
        assert paper.cites_target is True
        assert paper.uses_target_software is True
        assert paper.target_reference_ids == [41, 42]

    mapped = report["papers"]["mapped_columns"]
    assert set(mapped) == set(mappings.values())
    assert all(counts == {"source_non_null": 1, "target_non_null": 1} for counts in mapped.values())
    db.reset_engines()
