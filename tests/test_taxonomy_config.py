import inspect
from contextlib import contextmanager
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routers import papers as papers_api
from api.routers.papers import listing as papers_listing
from core.config_loader import ConfigLoader
from core.taxonomy_config import load_taxonomy_config
from paper_library.models import Base, Paper
from scripts.clean_legacy_taxonomy import clean_column_value, clean_session


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_default_taxonomy_is_compact_and_qmri_focused():
    taxonomy = load_taxonomy_config(CONFIG_DIR)
    dimensions = taxonomy["dimensions"]
    assert [item["label"] for item in dimensions["research_topics"]["items"]] == [
        "神经退行性疾病", "肿瘤", "心血管", "肌骨", "方法学",
    ]
    assert [item["label"] for item in dimensions["paradigms"]["items"]] == [
        "静息态扫描", "任务态", "纵向随访",
    ]
    assert [item["label"] for item in dimensions["study_design"]["items"]] == [
        "横断面", "纵向", "病例对照", "方法学验证",
    ]
    assert dimensions["tags"]["items"] == []
    assert dimensions["neuroscience_subtopics"]["items"] == []
    assert {item["label"] for item in dimensions["multimodal"]["items"]} == {
        "PET", "CT", "超声", "EEG",
    }
    assert {item["label"] for item in dimensions["multimodal_presence"]["items"]} == {"是", "否"}
    assert [item["label"] for item in dimensions["multimodal_timing"]["items"]] == [
        "实时/同步", "前后测", "分时/顺序",
    ]


def test_taxonomy_loader_honors_environment_override(tmp_path, monkeypatch):
    custom_path = tmp_path / "custom-taxonomy.yaml"
    custom_path.write_text(yaml.safe_dump({"dimensions": {"example": {"items": []}}}), encoding="utf-8")
    monkeypatch.setenv("TAXONOMY_CONFIG", str(custom_path))
    assert "example" in load_taxonomy_config()["dimensions"]


def test_config_options_are_loaded_from_prompts_models_and_taxonomy():
    config = ConfigLoader(CONFIG_DIR).load_all()
    from api.routers.config_options import build_config_options

    result = build_config_options(config)
    assert result["themes"]
    assert {item["value"] for item in result["models"]} >= {"auto"}
    assert "statuses" not in result
    assert "require_target_entity" in result["generation"]
    assert result["multimodal_timings"] == [
        {"value": "concurrent", "label": "实时/同步"},
        {"value": "pre_post", "label": "前后测"},
        {"value": "sequential", "label": "分时/顺序"},
    ]
    assert result["taxonomy"]["dimensions"]["tags"]["items"] == []


def _invoke_filter_options(session, monkeypatch):
    @contextmanager
    def session_context(**_kwargs):
        yield session

    monkeypatch.setattr(papers_listing, "get_session", session_context)
    params = {
        name: None
        for name in inspect.signature(papers_api.get_filter_options).parameters
        if name != "user"
    }
    return papers_api.get_filter_options(**params, user={"role": "admin"})


def test_filter_options_has_no_taxonomy_options_for_empty_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        result = _invoke_filter_options(session, monkeypatch)
    for name in (
        "research_topics", "neuroscience_subtopics", "diseases", "study_designs",
        "paradigms", "multimodal_techniques", "multimodal_timings", "multimodal",
        "tag_categories",
    ):
        assert result[name] == []
    assert result["classification_catalogs"]["research_topics"] == []
    assert result["age_range"] == {"min": None, "max": None}
    engine.dispose()


def test_filter_options_returns_observed_custom_values(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'custom.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(Paper(
            title="Custom classification",
            research_topics=["自定义主题"],
            subject_diseases=["自定义疾病"],
            study_design="自定义设计",
            paradigms=["自定义范式"],
            uses_multimodal=True,
            multimodal_methods=["自定义技术"],
            multimodal_relations=[{"method": "自定义技术", "timing": "自定义时序"}],
            tags=["自定义标签"],
        ))
        session.commit()
        result = _invoke_filter_options(session, monkeypatch)

    for field, value in (
        ("research_topics", "自定义主题"),
        ("diseases", "自定义疾病"),
        ("study_designs", "自定义设计"),
        ("paradigms", "自定义范式"),
        ("multimodal_techniques", "自定义技术"),
        ("multimodal_timings", "自定义时序"),
    ):
        option = next(option for option in result[field] if option["value"] == value)
        assert option["count"] == 1
        assert option["custom"] is True
        assert option["group"] == "其他/自定义"
    assert result["multimodal"] == [{
        "value": "true", "label": "是", "count": 1, "custom": False,
    }]
    assert result["tag_categories"][0]["name"] == "其他/自定义"
    assert result["tag_categories"][0]["tags"][0]["name"] == "自定义标签"
    engine.dispose()


def test_clean_column_value_drops_unknowns_and_can_keep_custom_values():
    taxonomy = load_taxonomy_config(CONFIG_DIR)
    cleaned, removed = clean_column_value(
        "research_topics", ["肿瘤", "未知主题"], taxonomy,
    )
    assert cleaned == ["肿瘤"]
    assert removed == 1
    preserved, removed = clean_column_value(
        "research_topics", ["肿瘤", "未知主题"], taxonomy, keep_custom=True,
    )
    assert preserved == ["肿瘤", "未知主题"]
    assert removed == 0


def test_clean_column_value_prunes_values_inside_classification_sources():
    taxonomy = load_taxonomy_config(CONFIG_DIR)
    cleaned, removed = clean_column_value(
        "classification_sources",
        {
            "research_topics": {
                "source": "auto",
                "evidence": {"肿瘤": ["title"], "旧主题": ["abstract"]},
                "llm_reviews": [{"value": "旧主题"}, {"value": "肿瘤"}],
            },
            "old_dimension": {"source": "legacy"},
        },
        taxonomy,
    )
    assert removed == 3
    assert cleaned == {
        "research_topics": {
            "source": "auto",
            "evidence": {"肿瘤": ["title"]},
            "llm_reviews": [{"value": "肿瘤"}],
        },
    }


def test_clean_script_dry_run_and_apply(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'clean.db'}")
    Base.metadata.create_all(engine)
    taxonomy = load_taxonomy_config(CONFIG_DIR)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        paper = Paper(
            title="Dirty classification",
            tags=["unconfigured-tag"],
            research_topics=["肿瘤", "unconfigured-topic"],
            subject_diseases=["神经退行性疾病", "unconfigured-disease"],
            study_design="unconfigured-design",
            paradigms=["静息态扫描", "unconfigured-paradigm"],
            multimodal_methods=["PET", "unconfigured-method"],
            multimodal_relations=[
                {"method": "PET", "timing": "concurrent"},
                {"method": "unconfigured-method", "timing": "unconfigured-timing"},
            ],
            classification_sources={"research_topics": {"source": "manual"}, "old_key": {"source": "old"}},
        )
        session.add(paper)
        session.commit()

        dry_stats = clean_session(session, taxonomy, apply=False)
        assert dry_stats["research_topics"] == 1
        session.expire_all()
        stored = session.query(Paper).one()
        assert stored.research_topics == ["肿瘤", "unconfigured-topic"]

        apply_stats = clean_session(session, taxonomy, apply=True)
        assert apply_stats == dry_stats
        session.expire_all()
        stored = session.query(Paper).one()
        assert stored.tags == []
        assert stored.research_topics == ["肿瘤"]
        assert stored.subject_diseases == ["神经退行性疾病"]
        assert stored.study_design is None
        assert stored.paradigms == ["静息态扫描"]
        assert stored.multimodal_methods == ["PET"]
        assert stored.multimodal_relations == [{"method": "PET", "timing": "concurrent"}]
        assert stored.classification_sources == {"research_topics": {"source": "manual"}}
    engine.dispose()


def test_frontend_does_not_contain_old_fixed_taxonomy_options():
    frontend_root = Path(__file__).resolve().parents[1] / "frontend" / "src"
    source = "\n".join(path.read_text(encoding="utf-8") for path in frontend_root.rglob("*.ts*") if path.is_file())
    removed_terms = [
        "".join(("运动", "想象")),
        "超" + "扫描",
        "神" + "内",
        "V" + "FT",
        "Str" + "oop",
    ]
    assert all(term not in source for term in removed_terms)
