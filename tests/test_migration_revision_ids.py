"""Alembic stores revision ids in alembic_version.version_num, a VARCHAR(32) on PostgreSQL."""

import re
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_revision_ids_fit_postgres_alembic_version_column():
    too_long = {}
    for path in VERSIONS.glob("*.py"):
        match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', path.read_text(encoding="utf-8"), re.M)
        assert match, f"{path.name} has no revision id"
        if len(match.group(1)) > 32:
            too_long[path.name] = match.group(1)
    assert not too_long, f"revision ids longer than 32 characters: {too_long}"
