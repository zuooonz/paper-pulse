import json
import os
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Tests use isolated credentials and databases instead of developer runtime settings.
os.environ["JWT_SECRET"] = "test-only-jwt-secret-not-for-production"
os.environ["ALLOW_PLAINTEXT_PASSWORDS"] = "true"
os.environ["CREDENTIALS_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")
os.environ["JOBS_INLINE"] = "true"
os.environ["LOCAL_USERS"] = json.dumps(
    {
        "admin": {"password": "test-admin-password", "name": "管理员", "role": "admin"},
        "reviewer": {"password": "test-reviewer-password", "name": "审核员", "role": "reviewer"},
        "reader": {"password": "test-reader-password", "name": "查看者", "role": "viewer"},
        "limited": {"password": "test-limited-password", "name": "受限用户", "role": "limited"},
    },
    ensure_ascii=False,
)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _set_runtime_paths(directory: Path, monkeypatch=None, database_url: str | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    values = {
        "DATABASE_URL": database_url or f"sqlite:///{directory / 'app.db'}",
        "DB_INIT_MODE": "create_all",
    }
    values.update({
        "STORAGE_ROOT": str(directory / "data"),
        "OUTPUT_ROOT": str(directory / "output"),
        "PUBLISH_DEBUG_DIR": str(directory / "tmp" / "publish_debug"),
    })
    for env_name, value in values.items():
        if monkeypatch is None:
            os.environ[env_name] = value
        else:
            monkeypatch.setenv(env_name, value)
    for obsolete_name in ("ACCOUNTS_DATABASE_URL", "DRAFTS_DATABASE_URL", "GOVERNANCE_DATABASE_URL"):
        if monkeypatch is None:
            os.environ.pop(obsolete_name, None)
        else:
            monkeypatch.delenv(obsolete_name, raising=False)


def pytest_sessionstart(session):
    """Point collection-time imports at a fresh session directory before collection."""
    runtime_dir = session.config._tmp_path_factory.getbasetemp() / "paper-pulse-runtime"
    _set_runtime_paths(runtime_dir)


@pytest.fixture(autouse=True)
def isolate_runtime_databases(tmp_path, monkeypatch):
    """Give each test independent storage and dispose cached SQLAlchemy engines."""
    test_database_url = os.environ.get("TEST_DATABASE_URL", "")
    use_postgres = test_database_url.startswith(("postgresql://", "postgresql+"))
    _set_runtime_paths(
        tmp_path,
        monkeypatch,
        database_url=test_database_url if use_postgres else None,
    )

    import paper_library.db as db
    from paper_library.models import Base
    from sqlalchemy import create_engine, event, text

    db.reset_engines()

    admin_engine = None
    schema_name = None
    if use_postgres:
        admin_engine = create_engine(test_database_url)
        schema_name = f"test_{uuid.uuid4().hex}"
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        def set_test_search_path(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET search_path TO "{schema_name}"')
            cursor.close()

        original_create_engine = db._create_engine

        def create_schema_engine(url, *, pool_options=None):
            engine = original_create_engine(url, pool_options=pool_options)
            if url.startswith(("postgresql://", "postgresql+")) and hasattr(engine, "dispatch"):
                event.listen(engine, "connect", set_test_search_path)
            return engine

        monkeypatch.setattr(db, "_create_engine", create_schema_engine)

    # Use the fast schema path for tests that call init_db implicitly.
    Base.metadata.create_all(db.get_engine())

    try:
        yield
    finally:
        db.reset_engines()
        if admin_engine is not None and schema_name is not None:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
            admin_engine.dispose()
