import argparse
import sys
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

import paper_library.db as db
from core.cli import add_db_arguments, prepare_database


def _configure_empty_database(tmp_path, monkeypatch):
    database_path = tmp_path / "cli-workspace" / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.delenv("DB_INIT_MODE", raising=False)
    monkeypatch.delenv("PAPER_PULSE_NO_MIGRATE", raising=False)
    db.reset_engines()
    return database_path


def test_prepare_database_migrates_empty_sqlite_to_head(tmp_path, monkeypatch):
    database_path = _configure_empty_database(tmp_path, monkeypatch)
    args = argparse.Namespace(
        workspace=str(tmp_path), config_dir="config", no_migrate=False
    )

    assert prepare_database(args) is True

    engine = db.get_engine(str(tmp_path))
    assert "papers" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()

    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert revision == script.get_current_head()
    assert database_path.exists()
    db.reset_engines()


def test_prepare_database_skips_upgrade_when_already_at_head(tmp_path, monkeypatch):
    _configure_empty_database(tmp_path, monkeypatch)
    args = argparse.Namespace(
        workspace=str(tmp_path), config_dir="config", no_migrate=False
    )
    assert prepare_database(args) is True

    def fail_upgrade(*_args, **_kwargs):
        raise AssertionError("upgrade should not run when the database is at head")

    monkeypatch.setattr(db, "_run_alembic_upgrade", fail_upgrade)
    assert db.ensure_database_current(str(tmp_path), "config") is False
    db.reset_engines()


def test_no_migrate_argument_and_environment_skip_database_setup(tmp_path, monkeypatch):
    database_path = _configure_empty_database(tmp_path, monkeypatch)
    parser = argparse.ArgumentParser()
    add_db_arguments(parser)
    args = parser.parse_args(["--no-migrate"])

    assert prepare_database(args, workspace=str(tmp_path)) is False
    assert not database_path.exists()

    monkeypatch.setenv("PAPER_PULSE_NO_MIGRATE", "1")
    assert prepare_database(argparse.Namespace(), workspace=str(tmp_path)) is False
    assert not database_path.exists()


def test_search_domain_dry_run_starts_with_empty_database(tmp_path, monkeypatch):
    database_path = _configure_empty_database(tmp_path, monkeypatch)

    from scripts import search_domain_papers

    monkeypatch.setattr(search_domain_papers, "search_pubmed_range", lambda *_args: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_domain_papers.py",
            "--workspace",
            str(tmp_path),
            "--years",
            "2020-2020",
            "--source",
            "pubmed",
            "--dry-run",
        ],
    )

    search_domain_papers.main()

    assert "papers" in inspect(db.get_engine(str(tmp_path))).get_table_names()
    assert database_path.exists()
    db.reset_engines()
