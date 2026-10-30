from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import inspect

from paper_library import db
from paper_library.models import Base


def test_alembic_upgrade_empty_sqlite_matches_metadata(tmp_path, monkeypatch):
    database_path = tmp_path / "empty-app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.delenv("DB_INIT_MODE", raising=False)
    db.reset_engines()

    # The shared test fixture creates tables for most tests; this case checks
    # the real Alembic bootstrap from an empty SQLite file.
    Base.metadata.drop_all(db.get_engine(str(tmp_path)))
    db.reset_engines()

    db.init_db(str(tmp_path))
    engine = db.get_engine(str(tmp_path))
    assert "affiliation_locations" in inspect(engine).get_table_names()
    author_location_fk = next(
        foreign_key
        for foreign_key in inspect(engine).get_foreign_keys("authors")
        if foreign_key["constrained_columns"] == ["canonical_affiliation_id"]
    )
    assert author_location_fk["referred_table"] == "affiliation_locations"
    assert author_location_fk["options"]["ondelete"] == "SET NULL"
    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        assert compare_metadata(migration_context, Base.metadata) == []
        revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    script = ScriptDirectory.from_config(Config(str(root / "alembic.ini")))
    assert revision == script.get_current_head()
    db.reset_engines()


def test_unused_schema_downgrade_restores_empty_legacy_columns_and_table(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import create_engine

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'unused-schema.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert "channel_credentials" not in inspect(connection).get_table_names()
        assert not {
            "audio_content", "video_script", "published_url"
        } & {column["name"] for column in inspect(connection).get_columns("article_drafts")}
        assert "retry_count" not in {
            column["name"] for column in inspect(connection).get_columns("publish_records")
        }
    engine.dispose()

    command.downgrade(config, "0011_drop_promotions")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        draft_columns = {
            column["name"]: column
            for column in inspect(connection).get_columns("article_drafts")
        }
        for name in ("audio_content", "video_script", "published_url"):
            assert draft_columns[name]["nullable"] is True
        retry_column = next(
            column for column in inspect(connection).get_columns("publish_records")
            if column["name"] == "retry_count"
        )
        assert retry_column["nullable"] is True
        assert retry_column["default"] in {"0", "(0)"}
        assert "channel_credentials" in inspect(connection).get_table_names()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM channel_credentials"
        ).scalar_one() == 0
        assert any(
            index["unique"]
            for index in inspect(connection).get_indexes("channel_credentials")
            if index["name"] == "ix_channel_credentials_channel"
        )
    engine.dispose()


def test_drop_wechat_channel_migration_preserves_detached_history(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'wechat-migration.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url

    command.upgrade(config, "0008_drop_paper_ppt")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        draft_id = connection.execute(
            text(
                "INSERT INTO article_drafts (draft_type, title, content, status) "
                "VALUES ('review', 'Migration fixture', 'body', 'approved')"
            )
        ).lastrowid
        account_id = connection.execute(
            text(
                "INSERT INTO channel_accounts "
                "(channel, account_name, credential_type, is_active) "
                "VALUES ('wechat', 'Removed account', 'cookie', 1)"
            )
        ).lastrowid
        for status in ("pending", "success"):
            connection.execute(
                text(
                    "INSERT INTO publish_records "
                    "(draft_id, account_id, channel, account_name, status, created_at) "
                    "VALUES (:draft_id, :account_id, 'wechat', 'Removed account', :status, CURRENT_TIMESTAMP)"
                ),
                {"draft_id": draft_id, "account_id": account_id, "status": status},
            )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM channel_accounts WHERE channel = 'wechat'")
        ).scalar_one() == 0
        records = connection.execute(
            text(
                "SELECT channel, account_id, status, error FROM publish_records "
                "WHERE channel = 'wechat' ORDER BY id"
            )
        ).mappings().all()
        assert [record["status"] for record in records] == ["cancelled", "success"]
        assert [record["error"] for record in records] == ["渠道已移除", None]
        assert all(record["account_id"] is None for record in records)
        assert all(record["channel"] == "wechat" for record in records)
        assert "content_wechat" not in {
            column["name"] for column in inspect(connection).get_columns("article_drafts")
        }
    engine.dispose()

    command.downgrade(config, "0008_drop_paper_ppt")
    engine = create_engine(database_url)
    content_column = next(
        column for column in inspect(engine).get_columns("article_drafts")
        if column["name"] == "content_wechat"
    )
    assert content_column["nullable"] is True
    engine.dispose()


def test_local_builtin_migration_converts_records_and_adds_partial_index(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'local-migration.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url

    command.upgrade(config, "0008_drop_paper_ppt")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        draft_ids = [
            connection.execute(
                text(
                    "INSERT INTO article_drafts (draft_type, title, content, status) "
                    "VALUES ('review', :title, 'body', 'approved')"
                ),
                {"title": title},
            ).lastrowid
            for title in ("Local migration one", "Local migration two")
        ]
        account_ids = [
            connection.execute(
                text(
                    "INSERT INTO channel_accounts "
                    "(channel, account_name, credential_type, is_active) "
                    "VALUES ('local', :name, 'none', 1)"
                ),
                {"name": name},
            ).lastrowid
            for name in ("Old local one", "Old local two")
        ]
        for account_id, idempotency_key in zip(account_ids, ("shared-key", "shared-key"), strict=True):
            connection.execute(
                text(
                    "INSERT INTO publish_records "
                    "(draft_id, account_id, channel, account_name, status, idempotency_key, created_at) "
                    "VALUES (:draft_id, :account_id, 'local', 'Old local name', 'success', "
                    ":idempotency_key, CURRENT_TIMESTAMP)"
                ),
                {
                    "draft_id": draft_ids[0],
                    "account_id": account_id,
                    "idempotency_key": idempotency_key,
                },
            )
        connection.execute(
            text(
                "INSERT INTO publish_records "
                "(draft_id, account_id, channel, account_name, status, idempotency_key, created_at) "
                "VALUES (:draft_id, :account_id, 'local', 'Old local name', 'pending', 'other-key', CURRENT_TIMESTAMP)"
            ),
            {"draft_id": draft_ids[1], "account_id": account_ids[0]},
        )
    engine.dispose()


    command.upgrade(config, "0010_local_builtin_channel")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM channel_accounts WHERE channel = 'local'")
        ).scalar_one() == 0
        records = connection.execute(
            text(
                "SELECT draft_id, channel, account_id, account_name, idempotency_key "
                "FROM publish_records ORDER BY id"
            )
        ).mappings().all()
        assert [(row["channel"], row["account_id"], row["account_name"]) for row in records] == [
            ("local", None, "本地 HTML"),
            ("local", None, "本地 HTML"),
            ("local", None, "本地 HTML"),
        ]
        assert [row["idempotency_key"] for row in records] == ["shared-key", None, "other-key"]
        index = next(
            item for item in inspect(connection).get_indexes("publish_records")
            if item["name"] == "uq_publish_record_local_idempotency"
        )
        assert bool(index["unique"])
        assert "channel = 'local'" in str(index["dialect_options"]["sqlite_where"])
    engine.dispose()

    command.downgrade(config, "0009_drop_wechat_channel")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert "uq_publish_record_local_idempotency" not in {
            item["name"] for item in inspect(connection).get_indexes("publish_records")
        }
        assert connection.execute(
            text("SELECT count(*) FROM channel_accounts WHERE channel = 'local'")
        ).scalar_one() == 0
    engine.dispose()


def test_drop_local_channel_migration_cancels_active_records_and_restores_index_on_downgrade(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'drop-local-channel.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url

    command.upgrade(config, "0015_paper_lifecycle")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        draft_id = connection.execute(text(
            "INSERT INTO article_drafts (draft_type, title, content, status) "
            "VALUES ('review', 'Migration fixture', 'body', 'approved')"
        )).lastrowid
        connection.execute(text(
            "INSERT INTO publish_records "
            "(draft_id, account_id, channel, account_name, status, error, created_at) "
            "VALUES (:draft_id, NULL, 'local', '本地 HTML', :status, 'old error', CURRENT_TIMESTAMP)"
        ), [{"draft_id": draft_id, "status": status} for status in (
            "pending", "scheduled", "publishing", "success", "failed", "cancelled",
        )])
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        records = connection.execute(text(
            "SELECT channel, status, error FROM publish_records ORDER BY id"
        )).mappings().all()
        assert [record["channel"] for record in records] == ["local"] * 6
        assert [record["status"] for record in records] == [
            "cancelled", "cancelled", "cancelled", "success", "failed", "cancelled",
        ]
        assert [record["error"] for record in records[:3]] == [
            "本地 HTML 渠道已移除",
        ] * 3
        assert [record["error"] for record in records[3:]] == ["old error"] * 3
        assert "uq_publish_record_local_idempotency" not in {
            item["name"] for item in inspect(connection).get_indexes("publish_records")
        }
    engine.dispose()

    command.downgrade(config, "0015_paper_lifecycle")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        index = next(
            item for item in inspect(connection).get_indexes("publish_records")
            if item["name"] == "uq_publish_record_local_idempotency"
        )
        assert index["unique"]
        assert "channel = 'local'" in str(index["dialect_options"]["sqlite_where"])
    engine.dispose()


def test_literature_search_migration_renames_jobs_and_paper_sources(tmp_path):
    import json

    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'literature-search-migration.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "0012_drop_unused_columns")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, kind, payload, status, priority, run_after, attempts, max_attempts, created_at, updated_at, result) "
                "VALUES ('legacy-search', 'monthly_search', :payload, 'queued', 0, CURRENT_TIMESTAMP, 0, 2, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :result)"
            ),
            {
                "payload": json.dumps({"request": {"mode": "recent", "months": 2}}),
                "result": json.dumps({"request": {"mode": "recent", "months": 3}}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO papers (title, cites_target, ingestion_status, article_status, brand_source, source) "
                "VALUES ('Legacy paper', 0, 'pending', 'not_generated', 'monthly_search', 'entity_monthly_search')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        job = connection.execute(
            text("SELECT kind, payload, result FROM jobs WHERE id = 'legacy-search'")
        ).mappings().one()
        paper = connection.execute(
            text("SELECT entity_source, source FROM papers WHERE title = 'Legacy paper'")
        ).mappings().one()
        assert job["kind"] == "literature_search"
        assert json.loads(job["payload"])["request"] == {"mode": "recent", "days": 60}
        assert json.loads(job["result"])["request"] == {"mode": "recent", "days": 90}
        assert paper == {"entity_source": "literature_search", "source": "entity_literature_search"}
    engine.dispose()

    command.downgrade(config, "0012_drop_unused_columns")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        job = connection.execute(
            text("SELECT kind, payload FROM jobs WHERE id = 'legacy-search'")
        ).mappings().one()
        paper = connection.execute(
            text("SELECT brand_source, source FROM papers WHERE title = 'Legacy paper'")
        ).mappings().one()
        assert job["kind"] == "monthly_search"
        assert json.loads(job["payload"])["request"] == {"mode": "recent", "months": 2}
        assert paper == {"brand_source": "monthly_search", "source": "entity_monthly_search"}
    engine.dispose()


def test_entity_column_and_limited_role_migration_round_trips(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'entity-role-migration.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "0013_rename_literature_search")

    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO papers "
                "(title, cites_target, ingestion_status, article_status, brand, brand_source, brand_confidence) "
                "VALUES ('Entity migration', 0, 'ready', 'none', 'Target Entity', 'manual', 'high')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO quota_policies "
                "(role, resource_type, period_type, limit_value, timezone, enabled, created_at_utc, updated_at_utc) "
                "VALUES ('sales', 'paper', 'day', 100, 'UTC', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO access_requests "
                "(requester_actor_id, requester_role, business_date, request_type, action, requested_count, "
                "paper_ids_json, reason, status, version, created_at_utc) "
                "VALUES ('local:limited', 'sales', '2026-01-01', 'exact_scope', 'paper.generate', 1, '[]', "
                "'migration fixture', 'pending', 1, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(event_id, occurred_at_utc, business_date, actor_id, username, role, login_source, action, "
                "resource_type, resource_count, result, details_json) "
                "VALUES ('role-migration', CURRENT_TIMESTAMP, '2026-01-01', 'local:limited', 'limited', 'sales', "
                "'local', 'auth.login', 'session', 0, 'success', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dingtalk_users (dingtalk_userid, internal_username, role) "
                "VALUES ('migration-user', 'limited', 'sales')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        paper_columns = {column["name"] for column in inspect(connection).get_columns("papers")}
        assert {"entity_label", "entity_source", "entity_confidence"} <= paper_columns
        assert not {"brand", "brand_source", "brand_confidence"} & paper_columns
        assert "ix_papers_entity_label" in {
            item["name"] for item in inspect(connection).get_indexes("papers")
        }
        paper = connection.execute(
            text("SELECT entity_label, entity_source, entity_confidence FROM papers WHERE title = 'Entity migration'")
        ).one()
        assert tuple(paper) == ("Target Entity", "manual", "high")
        assert connection.execute(text("SELECT role FROM quota_policies")).scalar_one() == "limited"
        assert connection.execute(text("SELECT requester_role FROM access_requests")).scalar_one() == "limited"
        assert connection.execute(text("SELECT role FROM audit_events")).scalar_one() == "limited"
        assert connection.execute(text("SELECT role FROM dingtalk_users")).scalar_one() == "limited"
    engine.dispose()

    command.downgrade(config, "0013_rename_literature_search")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        paper_columns = {column["name"] for column in inspect(connection).get_columns("papers")}
        assert {"brand", "brand_source", "brand_confidence"} <= paper_columns
        assert not {"entity_label", "entity_source", "entity_confidence"} & paper_columns
        assert connection.execute(text("SELECT role FROM quota_policies")).scalar_one() == "sales"
        assert connection.execute(text("SELECT requester_role FROM access_requests")).scalar_one() == "sales"
        assert connection.execute(text("SELECT role FROM audit_events")).scalar_one() == "sales"
        assert connection.execute(text("SELECT role FROM dingtalk_users")).scalar_one() == "sales"
    engine.dispose()

def test_paper_lifecycle_migration_maps_every_legacy_state_and_downgrades_lossily(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'paper-lifecycle.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "0014_rename_entity_columns")
    from importlib import import_module

    migration = import_module("migrations.versions.0015_paper_lifecycle")
    assert migration._map_legacy_lifecycle(None, None) == ("pending", "unreviewed", None)

    legacy_states = (
        ("pending", "pending", None),
        ("pending_enrichment", "pending_enrichment", "legacy metadata error"),
        ("processing", "processing", None),
        ("downloading", "downloading", None),
        ("pending_review", "pending_review", None),
        ("ready", "ready", None),
        ("pdf_missing", "pdf_missing", None),
        ("error", "error", "legacy processing error"),
        ("unknown", "legacy_other", "legacy unknown error"),
    )
    engine = create_engine(database_url)
    paper_ids = {}
    with engine.begin() as connection:
        for key, status, error in legacy_states:
            result = connection.execute(
                text(
                    "INSERT INTO papers "
                    "(title, cites_target, article_status, doi, year, journal_name, abstract, pdf_storage_path, ingestion_status, ingestion_error) "
                    "VALUES (:title, 0, 'not_generated', :doi, 2025, 'Synthetic Journal', 'Synthetic abstract', :pdf, :status, :error)"
                ),
                {
                    "title": f"Lifecycle migration {key}",
                    "doi": f"10.5555/lifecycle.{key}",
                    "pdf": "data/pdf/existing.pdf" if key == "ready" else None,
                    "status": status,
                    "error": error,
                },
            )
            paper_ids[key] = result.lastrowid
        author_id = connection.execute(
            text("INSERT INTO authors (name) VALUES ('Synthetic Author')")
        ).lastrowid
        connection.execute(
            text(
                "INSERT INTO paper_authors (paper_id, author_id, author_order, is_first, is_corresponding) "
                "VALUES (:paper_id, :author_id, 0, 1, 0)"
            ),
            {"paper_id": paper_ids["ready"], "author_id": author_id},
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, processing_status, processing_error, review_outcome, has_pdf, metadata_complete "
                "FROM papers"
            )
        ).mappings().all()
        mapped = {row["id"]: row for row in rows}
        expected_processing = {
            "pending": "pending", "pending_enrichment": "pending",
            "processing": "processing", "downloading": "processing",
            "pending_review": "done", "ready": "done", "pdf_missing": "done",
            "error": "failed", "unknown": "failed",
        }
        for key, wanted in expected_processing.items():
            assert mapped[paper_ids[key]]["processing_status"] == wanted
        assert mapped[paper_ids["ready"]]["review_outcome"] == "approved"
        assert mapped[paper_ids["pending_review"]]["review_outcome"] == "unreviewed"
        assert mapped[paper_ids["pdf_missing"]]["review_outcome"] == "unreviewed"
        assert bool(mapped[paper_ids["ready"]]["has_pdf"]) is True
        assert bool(mapped[paper_ids["pending"]]["has_pdf"]) is False
        assert bool(mapped[paper_ids["ready"]]["metadata_complete"]) is True
        assert bool(mapped[paper_ids["pending"]]["metadata_complete"]) is False
        assert mapped[paper_ids["pending_enrichment"]]["processing_error"] == "legacy metadata error"
        assert "legacy_other" in mapped[paper_ids["unknown"]]["processing_error"]
        assert "legacy unknown error" in mapped[paper_ids["unknown"]]["processing_error"]
    engine.dispose()

    command.downgrade(config, "0014_rename_entity_columns")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        restored = {
            row["id"]: row
            for row in connection.execute(
                text("SELECT id, ingestion_status, ingestion_error FROM papers")
            ).mappings()
        }
        expected_approximation = {
            "pending": "pdf_missing", "pending_enrichment": "pdf_missing",
            "processing": "processing", "downloading": "processing",
            "pending_review": "pending_review", "ready": "ready",
            "pdf_missing": "pending_review", "error": "error", "unknown": "error",
        }
        for key, wanted in expected_approximation.items():
            assert restored[paper_ids[key]]["ingestion_status"] == wanted
    engine.dispose()
