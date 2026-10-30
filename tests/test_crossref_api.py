"""Tests for crossref_api.py structured mode."""

import json
from pathlib import Path
from unittest.mock import patch, Mock
import requests

from core.crossref_api import clean_crossref_abstract, fetch_metadata_by_doi, extract_doi_from_text


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_JSON = FIXTURES_DIR / "crossref_sample.json"


def test_extract_doi_from_text():
    """Basic DOI extraction test."""
    text = "Some text 10.5555/qmri-mapping-test more text"
    assert extract_doi_from_text(text) == "10.5555/qmri-mapping-test"


def test_clean_crossref_abstract_decodes_nested_entities_and_jats():
    value = "<jats:p>Synthetic qMRI T1 estimate was 73.79 &amp;#xb1; 11.49 ms and p &amp;lt; 0.05.</jats:p>"

    assert clean_crossref_abstract(value) == "Synthetic qMRI T1 estimate was 73.79 ± 11.49 ms and p < 0.05."


def test_structured_mode_returns_correct_structure(monkeypatch):
    """Test structured=True returns the expected structure with all fields."""
    monkeypatch.setenv("CROSSREF_BASE_URL", "https://crossref.example.test")
    with open(SAMPLE_JSON, "r", encoding="utf-8") as f:
        sample_data = json.load(f)

    mock_resp = Mock()
    mock_resp.json.return_value = {"message": sample_data}
    mock_resp.raise_for_status.return_value = None

    with patch("core.crossref_api.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = fetch_metadata_by_doi("10.5555/qmri-mapping-test", structured=True)

    assert result["doi"] == "10.5555/qmri-mapping-test"
    assert "Synthetic qMRI T1 and T2" in result["title"]
    assert result["journal"]["title"] == "Journal of Imaging Tests"
    assert result["journal"]["issn"] == ["0000-0000"]
    assert result["journal"]["eissn"] == "0000-0001"
    assert result["year"] == 2024
    assert len(result["authors"]) == 3

    # Check first author
    first = result["authors"][0]
    assert first["given"] == "Alex"
    assert first["family"] == "Example"
    assert first["sequence"] == "first"
    assert first["affiliations"] == ["Example Imaging Institute"]

    # Check third author with ORCID
    third = result["authors"][2]
    assert third["given"] == "Jordan"
    assert third["family"] == "Researcher"
    assert len(third["affiliations"]) == 2
    assert "Quantitative MRI Research Centre" in third["affiliations"][1]
    assert third["orcid"] is not None
    assert "orcid" in third["orcid"]


def test_unstructured_backward_compatible(monkeypatch):
    """Test that structured=False (default) returns old format unchanged."""
    monkeypatch.setenv("CROSSREF_BASE_URL", "https://crossref.example.test")
    with open(SAMPLE_JSON, "r", encoding="utf-8") as f:
        sample_data = json.load(f)

    mock_resp = Mock()
    mock_resp.json.return_value = {"message": sample_data}
    mock_resp.raise_for_status.return_value = None

    with patch("core.crossref_api.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = fetch_metadata_by_doi("10.5555/qmri-mapping-test", structured=False)

    # Old format has authors_str, not authors
    assert "authors_str" in result
    assert "authors" not in result
    assert "journal" in result and isinstance(result["journal"], str)
    assert "published_date" in result
    assert result["doi"] == "10.5555/qmri-mapping-test"
    assert "Example" in result["authors_str"]


def test_structured_handles_404(monkeypatch):
    """Test structured mode handles 404 gracefully."""
    monkeypatch.setenv("CROSSREF_BASE_URL", "https://crossref.example.test")
    mock_resp = Mock()
    mock_404 = requests.HTTPError("404 Not Found")
    mock_404.response = Mock()
    mock_404.response.status_code = 404
    mock_resp.raise_for_status.side_effect = mock_404

    with patch("core.crossref_api.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = fetch_metadata_by_doi("invalid/doi", structured=True)

    assert result == {}


def test_structured_handles_empty_authors(monkeypatch):
    """Test structured mode with no authors."""
    monkeypatch.setenv("CROSSREF_BASE_URL", "https://crossref.example.test")
    sample_data = {
        "DOI": "10.1234/test",
        "title": ["Test Paper"],
        "container-title": ["Test Journal"],
        "published-print": {"date-parts": [[2023]]},
        "author": []
    }

    mock_resp = Mock()
    mock_resp.json.return_value = {"message": sample_data}
    mock_resp.raise_for_status.return_value = None

    with patch("core.crossref_api.requests.get") as mock_get:
        mock_get.return_value = mock_resp
        result = fetch_metadata_by_doi("10.1234/test", structured=True)

    assert result["doi"] == "10.1234/test"
    assert result["authors"] == []
