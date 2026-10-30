from core import entity_detector


def test_default_entity_directory_loads_qmri_examples():
    config = entity_detector.load_entities_config()

    assert config["target"] == "qmrlab"
    assert entity_detector.get_target_label() == "qMRLab"
    assert set(config["entities"]) == {"qmrlab", "hmri_toolbox"}
    assert config["entities"]["qmrlab"]["links"]["homepage"] == "https://qmrlab.org"
    assert config["entities"]["qmrlab"]["citation_dois"] == ["10.21105/joss.02343"]
    assert config["entities"]["hmri_toolbox"]["citation_dois"] == [
        "10.1016/j.neuroimage.2019.01.029"
    ]
    assert config["scoring"]["device"] == 3


def test_qmrlab_and_hmri_toolbox_detection_examples():
    positive_examples = (
        (
            "Methods: Quantitative MRI maps were processed with the qMRLab toolbox "
            "for relaxometry analysis.",
            "qMRLab",
        ),
        (
            "Methods: Quantitative MRI maps were analyzed with the hMRI toolbox "
            "for SPM in this neuroscience workflow.",
            "hMRI toolbox",
        ),
        (
            "Methods: An hMRI-toolbox workflow was used for quantitative MRI "
            "analysis and relaxometry mapping.",
            "hMRI toolbox",
        ),
        (
            "Methods: An hMRI package was used with SPM for quantitative MRI "
            "analysis and relaxometry mapping.",
            "hMRI toolbox",
        ),
    )
    for text, expected_label in positive_examples:
        label, confidence, scores = entity_detector.detect_entity(text)
        assert label == expected_label
        assert confidence in {"low", "medium", "high"}
        assert scores

    negative_examples = (
        "Methods: The qMRI results were calculated with a generic software package "
        "for relaxometry analysis and comparison.",
        "Methods: hMRI was mentioned as a generic abbreviation in this quantitative "
        "MRI study; no analysis package was described.",
    )
    for text in negative_examples:
        label, confidence, scores = entity_detector.detect_entity(text)
        assert label is None
        assert confidence is None
        assert scores == {}


def test_entity_directory_can_be_loaded_from_an_injected_path(tmp_path):
    config_path = tmp_path / "entities.yaml"
    config_path.write_text(
        """target: demo_scanner
entities:
  demo_scanner:
    label: DemoScanner
    devices: [Demo Q2]
    software: []
    company: [Demo Instruments]
    extra: []
    search:
      pmc_query: '"Demo Q2" AND MRI'
      epmc_query: '"Demo Q2" AND MRI'
      gs_queries: ['"Demo Q2" MRI']
confusions: {}
custom_built_patterns: []
display_map: {demo_scanner: DemoScanner}
scoring: {device: 3, software: 2, company: 2, extra: 1, min_score: 2, custom_built: 5, context_bonus: 1, high_confidence: 6, medium_confidence: 4, ambiguity_ratio: 0.7}
""",
        encoding="utf-8",
    )

    config = entity_detector.load_entities_config(config_path=config_path)
    detected, confidence, scores = entity_detector.detect_entity(
        "Methods: Images were acquired with the Demo Q2 scanner for quantitative MRI.",
        config=config,
    )

    assert config["target"] == "demo_scanner"
    assert detected == "DemoScanner"
    assert confidence == "medium"
    assert scores["demo_scanner"] == 4


def test_null_target_disables_target_label_and_target_scope(monkeypatch):
    config = {
        "target": None,
        "entities": {
            "sample_vendor": {
                "label": "SampleVendor",
                "devices": ["Sample MRI"],
                "software": [],
                "company": [],
                "extra": [],
            }
        },
        "confusions": {},
        "custom_built_patterns": [],
        "display_map": {"sample_vendor": "SampleVendor"},
        "scoring": {
            "device": 3,
            "software": 2,
            "company": 2,
            "extra": 1,
            "min_score": 2,
            "custom_built": 5,
            "context_bonus": 1,
            "high_confidence": 6,
            "medium_confidence": 4,
            "ambiguity_ratio": 0.7,
        },
    }
    assert entity_detector.get_target_label(config=config) is None

    from api.routers import authors

    monkeypatch.setattr(authors, "get_target_label", lambda: None)
    assert authors._scope_filter("target") == (" AND 1 = 0", {})


def test_author_scope_binds_configured_target_label(monkeypatch):
    from api.routers import authors

    target_label = "target' OR 1 = 1 --"
    monkeypatch.setattr(authors, "get_target_label", lambda: target_label)
    sql, params = authors._scope_filter("target")

    assert sql == " AND p.entity_label = :scope_target_label"
    assert params == {"scope_target_label": target_label}
