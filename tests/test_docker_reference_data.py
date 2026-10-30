"""Docker image and environment configuration for journal reference data."""

import fnmatch
import re
import sqlite3
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_copies_reference_data_outside_data_volume():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"(?m)^COPY\s+data/reference/\s+/app/reference/\s*$",
        dockerfile,
    )
    assert re.search(
        r"(?m)^ENV\s+JOURNAL_METRICS_DB=/app/reference/jcr\.db\s*$",
        dockerfile,
    )


def test_dockerignore_allows_reference_database_and_licenses():
    lines = [
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    def is_ignored(path):
        ignored = False
        for pattern in lines:
            include = pattern.startswith("!")
            pattern = pattern[1:] if include else pattern
            if fnmatch.fnmatchcase(path, pattern):
                ignored = not include
        return ignored

    for name in ("jcr.db", "NOTICE.md", "LICENSE.GPL-3.0"):
        assert not is_ignored(f"data/reference/{name}")


def test_compose_uses_image_reference_path_for_backend_services():
    compose = yaml.safe_load((PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    expected_path = "${JOURNAL_METRICS_DB:-/app/reference/jcr.db}"

    for service_name in ("api", "worker", "scheduler"):
        service = compose["services"][service_name]
        assert service["environment"]["JOURNAL_METRICS_DB"] == expected_path
        assert all("/app/reference" not in volume for volume in service.get("volumes", []))


def test_jcr_matcher_uses_journal_metrics_db_environment_path(tmp_path, monkeypatch):
    from paper_library.journal_enrichment import JcrMatcher

    db_path = tmp_path / "env-reference.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'CREATE TABLE "JCR2025" '
            '("Journal" TEXT, "ISSN" TEXT, "EISSN" TEXT, '
            '"IF(2025)" TEXT, "IF Quartile(2025)_1" TEXT)'
        )
        conn.execute(
            'INSERT INTO "JCR2025" VALUES (?, ?, ?, ?, ?)',
            ("Temporary Example Journal", "1234-5678", "", "2.4", "Q2"),
        )
        conn.commit()

    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    monkeypatch.setenv("JOURNAL_METRICS_DB", str(db_path))

    matcher = JcrMatcher().load()
    entry = matcher.match(issn="1234-5678")

    assert matcher.jcr_db_path == db_path.resolve()
    assert entry is not None
    assert entry.name == "Temporary Example Journal"
    assert entry.impact_factor == 2.4


def test_jcr_matcher_uses_reference_config_path_before_default(tmp_path, monkeypatch):
    from paper_library.journal_enrichment import JcrMatcher

    config_path = tmp_path / "reference.yaml"
    config_path.write_text(
        yaml.safe_dump({"journal_metrics": {"path": "configured.db"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    monkeypatch.delenv("JOURNAL_METRICS_DB", raising=False)

    matcher = JcrMatcher(config_path=config_path, workspace=tmp_path)

    assert matcher.jcr_db_path == (tmp_path / "configured.db").resolve()


def test_jcr_matcher_falls_back_to_default_reference_path(tmp_path, monkeypatch):
    from paper_library.journal_enrichment import JcrMatcher

    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    monkeypatch.delenv("JOURNAL_METRICS_DB", raising=False)

    matcher = JcrMatcher(config={"journal_metrics": {}}, workspace=tmp_path)

    assert matcher.jcr_db_path == (tmp_path / "data/reference/jcr.db").resolve()
