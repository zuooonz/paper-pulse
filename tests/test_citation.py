from paper_library.citation import (
    format_apa_author_list,
    format_apa_person_name,
    sanitize_apa_journal_name,
    sanitize_scholarly_markup,
)
from paper_library.crud import _build_apa_for_paper_data


def test_author_list_does_not_duplicate_final_period():
    assert format_apa_author_list(["Example, A.", "Sample, B."]) == "Example, A., & Sample, B."


def test_author_list_excludes_chinese_legacy_names():
    assert format_apa_author_list(["Example, A.", "单雪峰"]) == "Example, A."


def test_person_name_converts_given_family_to_apa():
    assert format_apa_person_name("Xuefeng Shan") == "Shan, X."


def test_person_name_preserves_existing_apa_format():
    assert format_apa_person_name("Example, J.") == "Example, J."


def test_journal_name_translates_preprint_placeholder():
    assert sanitize_apa_journal_name("预印本") == "Preprint"


def test_journal_name_removes_chinese_import_annotation():
    assert sanitize_apa_journal_name("Learning and Analytics（会议集）") == "Learning and Analytics"


def test_journal_name_rejects_chinese_only_title():
    assert sanitize_apa_journal_name("《上海交通大学学报（英文版）》") == ""


def test_scholarly_markup_preserves_text_and_normalizes_crossref_whitespace():
    value = "A\n  <SCP>qMRI</SCP>\n  ‐ <scp>EMG</scp> study &amp; review"
    assert sanitize_scholarly_markup(value) == "A qMRI‐EMG study & review"


def test_import_removes_scp_tags_from_title_and_apa():
    paper_data = {
        "title": "A synthetic qMRI mapping study",
        "journal_name": "Journal of Testing",
        "year": 2026,
    }
    _build_apa_for_paper_data(paper_data, {"author": []})
    assert paper_data["apa_citation"] == "(2026). A synthetic qMRI mapping study. Journal of Testing"


def test_import_apa_filters_chinese_metadata_and_has_single_final_period():
    paper_data = {
        "title": "A synthetic import",
        "journal_name": "Journal of Testing（会议集）",
        "year": 2026,
        "doi": "10.1000/example",
    }
    raw = {
        "author": [
            {"given": "Avery", "family": "Example"},
            {"given": "雪", "family": "单"},
            {"given": "Blair", "family": "Sample"},
        ]
    }

    _build_apa_for_paper_data(paper_data, raw)

    assert paper_data["apa_citation"] == (
        "Example, A., & Sample, B. (2026). A synthetic import. "
        "Journal of Testing https://doi.org/10.1000/example"
    )
    assert "单" not in paper_data["apa_citation"]
    assert "D.." not in paper_data["apa_citation"]
