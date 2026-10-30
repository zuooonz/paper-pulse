from pathlib import Path

from core.config_loader import ConfigLoader
from src.api.routers.papers import _validate_word_report_data


def test_report_profile_is_valid_yaml():
    workspace = Path(__file__).resolve().parents[1]

    profile = ConfigLoader(workspace / "config").load_profile("report")

    assert profile["system_prompt"]
    assert profile["defaults_override"]["max_output_tokens"] == 16000
    overview = next(
        item for item in profile["report_structure"] if item["section"] == "概述"
    )
    assert "报告范围" in overview["guidance"]
    assert "论文数量" in overview["guidance"]
    assert "植入广告" not in overview["guidance"]


def test_word_report_validation_rejects_partial_json():
    errors = _validate_word_report_data({
        "report_title": "标题",
        "overview": "这是一段足够长的概述。" * 10,
    })

    assert any("章节数量不足" in error for error in errors)
