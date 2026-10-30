from api.routers import papers


def _entities_config():
    return {
        "target": "demo_scanner",
        "entities": {
            "demo_scanner": {
                "label": "DemoScanner",
                "devices": ["Demo Scanner"],
                "software": [],
                "company": [],
                "extra": [],
            }
        },
        "confusions": {},
        "custom_built_patterns": [],
        "display_map": {"demo_scanner": "DemoScanner"},
        "scoring": {
            "device": 3,
            "software": 2,
            "company": 2,
            "extra": 1,
            "min_score": 2,
            "custom_built": 5,
            "context_bonus": 1,
            "high_confidence": 5,
            "medium_confidence": 3,
            "ambiguity_ratio": 0.7,
        },
    }


def test_doi_import_does_not_mark_entity_without_entity_match():
    metadata = {
        "title": "A general study of functional imaging methods and their applications",
        "abstract": "This paper describes a broad imaging study with participants and standard statistical analyses.",
    }

    fields = papers._doi_import_entity_fields(
        metadata,
        mark_as_target=False,
        entities_config=_entities_config(),
    )

    assert fields == {}


def test_doi_import_detects_target_in_crossref_metadata():
    metadata = {
        "raw": {
            "title": ["Functional imaging analysis using the Demo Scanner system"],
            "abstract": (
                "This study used a standard protocol for participant imaging and analyzed the "
                "resulting measurements across several experimental conditions."
            ),
        }
    }

    fields = papers._doi_import_entity_fields(
        metadata,
        mark_as_target=False,
        entities_config=_entities_config(),
    )

    assert fields == {
        "entity_label": "DemoScanner",
        "entity_source": "doi_metadata",
        "entity_confidence": "medium",
        "cites_target": True,
    }


def test_doi_import_mark_as_target_sets_entity_fields():
    fields = papers._doi_import_entity_fields(
        {},
        mark_as_target=True,
        entities_config=_entities_config(),
    )

    assert fields == {"entity_label": "DemoScanner", "entity_source": "doi_import"}


def test_doi_import_target_option_defaults_false_and_environment_overrides(tmp_path, monkeypatch):
    imports_path = tmp_path / "imports.yaml"
    imports_path.write_text("doi:\n  mark_as_target: false\n", encoding="utf-8")
    monkeypatch.delenv("DOI_IMPORT_MARK_AS_TARGET", raising=False)

    assert papers._doi_import_mark_as_target(tmp_path) is False

    imports_path.write_text("doi:\n  mark_as_target: true\n", encoding="utf-8")
    assert papers._doi_import_mark_as_target(tmp_path) is True

    monkeypatch.setenv("DOI_IMPORT_MARK_AS_TARGET", "false")
    assert papers._doi_import_mark_as_target(tmp_path) is False
    monkeypatch.setenv("DOI_IMPORT_MARK_AS_TARGET", "true")
    assert papers._doi_import_mark_as_target(tmp_path) is True
