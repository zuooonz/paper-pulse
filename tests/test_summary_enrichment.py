from scripts.enrich_conclusions_highlights import (
    build_evidence,
    extract_explicit_abstract,
    valid_highlights,
)


def test_extract_explicit_spaced_abstract():
    text = """Title and authors
A B S T R A C T
This study examined tissue characteristics with quantitative MRI. Participants completed a motor task and the results showed task-related activation in motor regions.
K E Y W O R D S
qMRI; T1 mapping
1. Introduction
Long introduction text.
"""
    result = extract_explicit_abstract(text)
    assert result is not None
    assert result.startswith("This study examined")
    assert "Introduction" not in result


def test_build_evidence_prefers_existing_abstract():
    evidence_type, evidence = build_evidence({
        "abstract": "This is the original abstract.",
        "abstract_zh": None,
        "extracted_text": "unrelated full text",
    })
    assert evidence_type == "原文摘要"
    assert evidence == "This is the original abstract."


def test_build_evidence_uses_bounded_sections_without_abstract():
    text = """Results
Task-related oxygenation increased in the motor region during movement.
Discussion
The pattern was consistent with cortical engagement during the task.
Conclusion
The evidence supports feasibility for task-state assessment.
References
Reference list.
"""
    evidence_type, evidence = build_evidence({"abstract": None, "abstract_zh": None, "extracted_text": text})
    assert evidence_type == "正文证据片段"
    assert "[Results]" in evidence
    assert "Reference list" not in evidence
    assert len(evidence) <= 4200


def test_highlights_require_exactly_three_nontrivial_items():
    assert valid_highlights(["研究采用 T1 mapping 定量评估组织特征", "结果显示目标脑区出现任务相关响应变化", "发现为客观评估任务状态提供了实验依据"]) is not None
    assert valid_highlights(["只有一条"]) is None
