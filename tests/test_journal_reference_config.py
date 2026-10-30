"""Journal reference matching against small fictional SQLite fixtures."""

import sqlite3

import yaml


def _create_configured_tables(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'CREATE TABLE "JCR2025" '
            '("Journal" TEXT, "ISSN" TEXT, "EISSN" TEXT, "IF(2025)" TEXT, "IF Quartile(2025)_1" TEXT)'
        )
        conn.execute(
            'INSERT INTO "JCR2025" VALUES (?, ?, ?, ?, ?)',
            ("Fictional Journal of Example Studies", "1111-1111", "2222-2222", "3.7", "Q2"),
        )
        conn.execute(
            'CREATE TABLE "FQBJCR2025" '
            '("Journal" TEXT, "ISSN/EISSN" TEXT, "大类" TEXT, "大类分区" TEXT, "Top" TEXT)'
        )
        conn.execute(
            'INSERT INTO "FQBJCR2025" VALUES (?, ?, ?, ?, ?)',
            ("Fictional Journal of Example Studies", "1111-1111 / 2222-2222", "虚构学科", "2区", "否"),
        )
        conn.execute(
            'CREATE TABLE "XR2026" '
            '("Journal" TEXT, "ISSN" TEXT, "EISSN" TEXT, "大类新锐分区" TEXT, "Top" TEXT)'
        )
        conn.execute(
            'INSERT INTO "XR2026" VALUES (?, ?, ?, ?, ?)',
            ("Fictional Journal of Example Studies", "1111-1111", "2222-2222", "1区", "是"),
        )
        conn.commit()


def test_reference_config_issn_and_name_matching(tmp_path, monkeypatch):
    from paper_library.journal_enrichment import JcrMatcher

    db_path = tmp_path / "fictional-reference.db"
    _create_configured_tables(db_path)
    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    monkeypatch.setenv("JOURNAL_METRICS_DB", str(db_path))

    matcher = JcrMatcher().load()
    by_issn = matcher.match(issn="2222-2222")
    by_name = matcher.match(journal_name="Fictional Journal of Example Study")

    assert by_issn is not None
    assert by_issn.name == "Fictional Journal of Example Studies"
    assert by_issn.impact_factor == 3.7
    assert by_issn.cas_partition == "Q2"
    assert by_issn.emerging_partition == "Q1(Top)"
    assert by_name is by_issn


def test_missing_reference_tables_are_skipped_with_one_warning(tmp_path, monkeypatch, caplog):
    from paper_library.journal_enrichment import JcrMatcher

    db_path = tmp_path / "partial-reference.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'CREATE TABLE "JCR2025" '
            '("Journal" TEXT, "ISSN" TEXT, "EISSN" TEXT, "IF(2025)" TEXT, "IF Quartile(2025)_1" TEXT)'
        )
        conn.execute(
            'INSERT INTO "JCR2025" VALUES (?, ?, ?, ?, ?)',
            ("Imaginary Methods Review", "3333-3333", "", "1.2", "Q3"),
        )
        conn.commit()
    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    monkeypatch.setenv("JOURNAL_METRICS_DB", str(db_path))

    matcher = JcrMatcher().load()
    matcher.load()

    assert matcher.match(issn="3333-3333").cas_partition is None
    assert sum("FQBJCR2025" in record.message for record in caplog.records) == 1


def test_custom_reference_table_and_column_mapping(tmp_path, monkeypatch):
    from paper_library.journal_enrichment import JcrMatcher

    db_path = tmp_path / "mapped-reference.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            'CREATE TABLE "FictionalMetrics" '
            '("Title" TEXT, "Print ISSN" TEXT, "Online ISSN" TEXT, "Annual IF" TEXT, "Rank" TEXT)'
        )
        conn.execute(
            'INSERT INTO "FictionalMetrics" VALUES (?, ?, ?, ?, ?)',
            ("Imaginary Data Journal", "4444-4444", "5555-5555", "2.1", "Q1"),
        )
        conn.commit()
    config_path = tmp_path / "reference.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "journal_metrics": {
                    "path": "ignored.db",
                    "fuzzy_threshold": 0.91,
                    "tables": {
                        "jcr": {
                            "table": "FictionalMetrics",
                            "journal": "Title",
                            "issn": "Print ISSN",
                            "eissn": "Online ISSN",
                            "impact_factor": "Annual IF",
                            "quartile": "Rank",
                        }
                    },
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFERENCE_CONFIG", str(config_path))
    monkeypatch.setenv("JOURNAL_METRICS_DB", str(db_path))

    matcher = JcrMatcher().load()
    entry = matcher.match(journal_name="Imaginary Data Journal")

    assert matcher.jcr_db_path == db_path
    assert matcher.fuzzy_threshold == 0.91
    assert entry is not None
    assert entry.issn == "4444-4444"
    assert entry.eissn == "5555-5555"
    assert entry.impact_factor == 2.1
    assert entry.jcr_quartile == "Q1"
