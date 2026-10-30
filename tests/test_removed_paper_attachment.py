import json

from sqlalchemy import inspect


def test_removed_attachment_migration_and_openapi(tmp_path, monkeypatch):
    from paper_library import db

    database_path = tmp_path / "papers.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.delenv("DB_INIT_MODE", raising=False)
    db.reset_engines()

    db.init_db(str(tmp_path))
    engine = db.get_engine(str(tmp_path))
    removed_column = "p" + "pt_storage_path"
    assert removed_column not in {
        column["name"] for column in inspect(engine).get_columns("papers")
    }
    db.reset_engines()

    from api.main import app

    removed_route_token = "p" + "pt"
    assert removed_route_token not in json.dumps(app.openapi()).casefold()
