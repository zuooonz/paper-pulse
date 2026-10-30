import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_drop_promotion_tables_and_openapi_filters(tmp_path):
    root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'drop-promotions.db'}"
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = database_url

    command.upgrade(config, "0010_local_builtin_channel")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        assert {"promotion_records", "paper_promotion_links"} <= set(
            inspect(connection).get_table_names()
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        table_names = set(inspect(connection).get_table_names())
        assert "promotion_records" not in table_names
        assert "paper_promotion_links" not in table_names
    engine.dispose()

    command.downgrade(config, "0010_local_builtin_channel")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert {"promotion_records", "paper_promotion_links"} <= set(inspector.get_table_names())
        record_indexes = {index["name"] for index in inspector.get_indexes("promotion_records")}
        assert record_indexes == {
            "ix_promotion_records_channel",
            "ix_promotion_records_content_hash",
            "ix_promotion_records_match_status",
            "ix_promotion_records_promotion_type",
            "ix_promotion_records_published_at",
        }
        link_indexes = {index["name"] for index in inspector.get_indexes("paper_promotion_links")}
        assert link_indexes == {
            "ix_paper_promotion_links_paper_id",
            "ix_paper_promotion_links_promotion_id",
            "ix_paper_promotion_links_verification_status",
        }
    engine.dispose()

    from api.main import app

    openapi = app.openapi()
    paper_list = openapi["paths"]["/api/papers"]["get"]
    filter_options = openapi["paths"]["/api/papers/filter-options"]["get"]
    assert not any("promotion" in param["name"].casefold() for param in paper_list["parameters"])
    assert not any("promotion" in param["name"].casefold() for param in filter_options["parameters"])
    assert "promotion" not in json.dumps(paper_list["responses"]).casefold()
