"""Database connection manager for the unified application database."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from urllib.parse import unquote, urlsplit

import yaml
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

from paper_library.models import Base
from paper_library.governance_models import QuotaPolicy
from paper_library import author_models as _author_models  # noqa: F401  (register all tables on Base.metadata)
from paper_library.time_utils import BUSINESS_TIMEZONE

_engine: Engine | None = None
_session_factory: sessionmaker | None = None

logger = logging.getLogger(__name__)


def reset_engines() -> None:
    """Dispose the shared engine and clear the process-wide session factory."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = _session_factory = None


def _resolve_sqlite_url(database_url: str, workspace: str) -> str:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database:
        return database_url
    if parsed.database == ":memory:" or parsed.database.startswith("file:"):
        return database_url
    if not os.path.isabs(parsed.database):
        parsed = parsed.set(database=os.path.abspath(os.path.join(workspace, parsed.database)))
    return parsed.render_as_string(hide_password=False)


def load_database_url(workspace: str = ".", config_dir: str = "config") -> str:
    """Load the single database URL and resolve SQLite paths from workspace."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        config_path = os.path.join(workspace, config_dir, "runtime.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as stream:
                config = yaml.safe_load(stream) or {}
            database_url = (config.get("database") or {}).get("url")
    if not database_url:
        database_url = "sqlite:///data/app.db"
    return _resolve_sqlite_url(database_url, workspace)


def _ensure_sqlite_parent(database_url: str) -> None:
    parsed_url = make_url(database_url)
    database_path = parsed_url.database
    if parsed_url.get_backend_name() == "sqlite" and database_path and database_path != ":memory:" and not database_path.startswith("file:"):
        os.makedirs(os.path.dirname(os.path.abspath(database_path)), exist_ok=True)


def _apply_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Apply SQLite performance and integrity settings to each connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-8000")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _configured_pool_options(
    workspace: str | None = None,
    config_dir: str | None = None,
) -> dict[str, int]:
    defaults = {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}
    workspace = workspace or os.environ.get("WORKSPACE", ".")
    config_dir = config_dir or os.environ.get("CONFIG_DIR", "config")
    config_path = os.path.join(workspace, config_dir, "runtime.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
        database_config = config.get("database") or {}
        for key in defaults:
            if database_config.get(key) is not None:
                defaults[key] = int(database_config[key])

    for env_name, key in (
        ("DB_POOL_SIZE", "pool_size"),
        ("DB_MAX_OVERFLOW", "max_overflow"),
        ("DB_POOL_RECYCLE", "pool_recycle"),
    ):
        if os.environ.get(env_name) is not None:
            defaults[key] = int(os.environ[env_name])
    return defaults


def _create_engine(url: str, *, pool_options: dict[str, int] | None = None) -> Engine:
    """Create the shared engine with dialect-appropriate pooling."""
    parsed_url = make_url(url)
    if parsed_url.get_backend_name() == "sqlite":
        _ensure_sqlite_parent(url)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=False,
            poolclass=NullPool,
        )
        event.listen(engine, "connect", _apply_sqlite_pragmas)
        return engine

    try:
        engine_options = {
            "echo": False,
            "poolclass": QueuePool,
            "pool_pre_ping": True,
            **(pool_options or _configured_pool_options()),
        }
        statement_timeout_ms = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "0"))
        if parsed_url.get_backend_name() == "postgresql" and statement_timeout_ms > 0:
            engine_options["connect_args"] = {
                "options": f"-c statement_timeout={statement_timeout_ms}"
            }
        return create_engine(
            url,
            **engine_options,
        )
    except ModuleNotFoundError as exc:
        if (
            parsed_url.get_backend_name() == "postgresql"
            and exc.name in {"psycopg", "psycopg2"}
        ):
            raise RuntimeError(
                "缺少 PostgreSQL 驱动，请安装 paper-pulse[postgres]；"
                "连接地址请使用 postgresql+psycopg://"
            ) from exc
        raise


def get_engine(workspace: str = ".", config_dir: str = "config") -> Engine:
    """Get or create the one application database engine."""
    global _engine
    if _engine is None:
        database_url = load_database_url(workspace, config_dir)
        if make_url(database_url).get_backend_name() == "sqlite":
            _engine = _create_engine(database_url)
        else:
            _engine = _create_engine(
                database_url,
                pool_options=_configured_pool_options(workspace, config_dir),
            )
    return _engine


def get_session_factory(workspace: str = ".", config_dir: str = "config") -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(workspace, config_dir),
            expire_on_commit=False,
        )
    return _session_factory


@contextmanager
def _session_scope(factory: sessionmaker) -> Generator[Session, None, None]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_session(workspace: str = ".", config_dir: str = "config") -> Generator[Session, None, None]:
    with _session_scope(get_session_factory(workspace, config_dir)) as session:
        yield session


def _alembic_config(database_url: str, workspace: str, config_dir: str):
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[2]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    alembic_config.attributes["database_url"] = database_url
    alembic_config.attributes["workspace"] = workspace
    alembic_config.attributes["config_dir"] = config_dir
    return alembic_config


def _run_alembic_upgrade(database_url: str, workspace: str, config_dir: str) -> None:
    from alembic import command

    alembic_config = _alembic_config(database_url, workspace, config_dir)
    command.upgrade(alembic_config, "head")


def _alembic_head(workspace: str, config_dir: str) -> str:
    from alembic.script import ScriptDirectory

    database_url = load_database_url(workspace, config_dir)
    return ScriptDirectory.from_config(
        _alembic_config(database_url, workspace, config_dir)
    ).get_current_head()


def _current_alembic_revision(database_url: str, workspace: str, config_dir: str) -> str | None:
    from alembic.migration import MigrationContext

    with get_engine(workspace, config_dir).connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@contextmanager
def _database_init_lock(database_url: str, workspace: str, config_dir: str):
    """Serialize schema initialization when multiple API workers start together."""
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() in {"postgres", "postgresql"}:
        lock_key = 0x4A4C4F434B
        connection = get_engine(workspace, config_dir).connect()
        connection.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": lock_key})
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})
            connection.close()
        return

    database_path = parsed_url.database
    if parsed_url.get_backend_name() != "sqlite" or not database_path or database_path == ":memory:":
        yield
        return

    if database_path.startswith("file:"):
        try:
            uri = urlsplit(database_path)
            uri_path = unquote(uri.path)
            if uri.netloc and uri.netloc != "localhost":
                uri_path = f"//{uri.netloc}{uri_path}"
            if uri_path == ":memory:" or parsed_url.query.get("mode") == "memory":
                yield
                return
            if not uri_path:
                logger.warning("Could not resolve SQLite file URI for init lock: missing file path")
                yield
                return
            database_file = Path(uri_path).expanduser().resolve()
        except (OSError, ValueError) as exc:
            logger.warning("Could not resolve SQLite file URI for init lock: %s", exc)
            yield
            return
    else:
        database_file = Path(database_path).expanduser().resolve()

    lock_path = Path(str(database_file) + ".init.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _seed_default_quota_policy(workspace: str, config_dir: str) -> None:
    with get_session(workspace, config_dir) as session:
        existing_policy = session.query(QuotaPolicy).filter_by(
            role="limited", resource_type="paper", period_type="day"
        ).first()
        if existing_policy is None:
            session.add(QuotaPolicy(
                role="limited",
                resource_type="paper",
                period_type="day",
                limit_value=100,
                timezone=BUSINESS_TIMEZONE,
                enabled=False,
            ))


def ensure_database_current(
    workspace: str = ".",
    config_dir: str = "config",
    *,
    _seed_defaults: bool = False,
) -> bool:
    """Upgrade the application schema to Alembic head when it is outdated.

    Returns True only when an Alembic upgrade command was executed. The
    create_all test mode keeps its historical behavior and is never reported
    as an Alembic migration.
    """
    database_url = load_database_url(workspace, config_dir)
    _ensure_sqlite_parent(database_url)

    create_all_mode = os.environ.get("DB_INIT_MODE", "alembic").lower() == "create_all"
    if not create_all_mode:
        head = _alembic_head(workspace, config_dir)
        if _current_alembic_revision(database_url, workspace, config_dir) == head:
            if _seed_defaults:
                # init_db has historically serialized schema setup and seeding.
                with _database_init_lock(database_url, workspace, config_dir):
                    _seed_default_quota_policy(workspace, config_dir)
            return False

    migrated = False
    with _database_init_lock(database_url, workspace, config_dir):
        if create_all_mode:
            Base.metadata.create_all(get_engine(workspace, config_dir))
        else:
            head = _alembic_head(workspace, config_dir)
            if _current_alembic_revision(database_url, workspace, config_dir) != head:
                _run_alembic_upgrade(database_url, workspace, config_dir)
                migrated = True

        if _seed_defaults:
            _seed_default_quota_policy(workspace, config_dir)
    return migrated


def init_db(
    workspace: str = ".",
    config_dir: str = "config",
    *,
    seed_defaults: bool = True,
) -> None:
    """Upgrade the unified schema and seed its default quota policy."""
    ensure_database_current(workspace, config_dir, _seed_defaults=seed_defaults)
