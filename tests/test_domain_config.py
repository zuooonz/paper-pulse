from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.article_builder import build_messages
from core.config_loader import ConfigLoader
from core.domain_config import (
    generation_requires_target_entity,
    load_domain,
    paper_matches_generation_scope,
    video_style_prompt,
)


def test_default_domain_package_contains_qmri_search_and_prompt_fields():
    domain = load_domain()

    assert domain["key"] == "qmri"
    assert domain["short_name"] == "qMRI"
    assert "T1 mapping" in domain["aliases"]
    assert domain["search"]["pubmed_query"]
    assert domain["search"]["epmc_query"]
    assert domain["search"]["gs_queries"]
    assert domain["prompt"]["required_terms"] == []


def test_load_domain_accepts_environment_path_and_injected_config(tmp_path, monkeypatch):
    custom = {"key": "demo", "name": "示例领域", "short_name": "示例"}
    path = tmp_path / "domain.yaml"
    path.write_text(yaml.safe_dump(custom, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("DOMAIN_CONFIG", str(path))

    assert load_domain()["key"] == "demo"
    assert load_domain(config={"key": "injected"}) == {"key": "injected"}


def test_profile_and_prompt_placeholders_render_without_format_errors(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "domain.yaml").write_text(
        "key: demo\nname: 示例领域\nshort_name: 示例\nprompt:\n  domain_intro: 这是领域介绍。\n  strengths_section_title: 方法适用性\n",
        encoding="utf-8",
    )
    (config_dir / "profile_test.yaml").write_text(
        "system_prompt: '围绕 {domain_name} 展开。{domain_intro}'\nheading: '{strengths_section_title}'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOMAIN_CONFIG", str(config_dir / "domain.yaml"))

    profile = ConfigLoader(config_dir).load_profile("test")
    assert profile["system_prompt"] == "围绕 示例领域 展开。这是领域介绍。"
    assert profile["heading"] == "方法适用性"


def test_article_builder_renders_known_placeholders_and_keeps_other_braces(tmp_path, monkeypatch):
    config_path = tmp_path / "domain.yaml"
    config_path.write_text(
        "key: demo\nname: 示例领域\nshort_name: 示例\nprompt:\n  domain_intro: 依据论文方法展开。\n  required_terms: [示例术语]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOMAIN_CONFIG", str(config_path))
    messages = build_messages(
        rec={},
        themes=[],
        prompts_cfg={
            "system_prompt": "{domain_name}：{domain_intro} {required_terms} {target_label} {undefined_key} {{\"json\": 1}}",
            "article_structure": [],
        },
        extracted_text="论文正文",
        cites_target=False,
    )

    system_prompt = messages[0]["content"]
    assert "示例领域：依据论文方法展开。" in system_prompt
    assert "示例术语" in system_prompt
    assert "{undefined_key}" in system_prompt
    assert '{"json": 1}' in system_prompt


def test_generation_scope_defaults_to_target_entity_and_can_be_disabled():
    assert generation_requires_target_entity({}) is True
    assert paper_matches_generation_scope("Target Entity", "Target Entity", domain={})
    assert not paper_matches_generation_scope("Other Entity", "Target Entity", domain={})

    any_entity = {"generation": {"require_target_entity": False}}
    assert not generation_requires_target_entity(any_entity)
    assert paper_matches_generation_scope("Other Entity", "Target Entity", domain=any_entity)
    assert paper_matches_generation_scope(None, None, domain=any_entity)


def test_video_style_prompts_come_from_domain_with_a_neutral_fallback():
    configured = load_domain()
    assert video_style_prompt("professional", configured) == configured["video_style"]["prompts"]["professional"]

    prompt = video_style_prompt("professional", {"name": "Example field"})
    assert prompt == "学术风格：采用与论文主题相符的示意动画、数据可视化或研究场景画面。"
    assert video_style_prompt("unknown", {}) == prompt


def test_article_builder_only_adds_target_entity_note_for_matching_papers(monkeypatch):
    from core import entity_detector

    monkeypatch.setattr(entity_detector, "get_target_label", lambda: "Target Entity")
    prompts = {"system_prompt": "你是科研编辑。", "article_structure": []}

    target_messages = build_messages(
        rec={"entity_label": "Target Entity"},
        themes=[],
        prompts_cfg=prompts,
        extracted_text="论文正文",
        cites_target=True,
    )
    other_messages = build_messages(
        rec={"entity_label": "Other Entity"},
        themes=[],
        prompts_cfg=prompts,
        extracted_text="论文正文",
        cites_target=True,
    )

    assert "Target Entity 课题标注要求" in target_messages[0]["content"]
    assert "Target Entity 课题标注要求" not in other_messages[0]["content"]
