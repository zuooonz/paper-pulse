"""Tests for DOI normalization and validation module."""

import pytest

from paper_library.doi import normalize_doi, is_valid_doi


@pytest.mark.parametrize(
    "input_value,expected",
    [
        # Standard DOI
        ("10.1038/nature12373", "10.1038/nature12373"),
        # URL form
        ("https://doi.org/10.1038/nature12373", "10.1038/nature12373"),
        ("http://doi.org/10.1038/nature12373", "10.1038/nature12373"),
        # doi: prefix with mixed case
        ("doi:10.1038/NATURE12373", "10.1038/nature12373"),
        ("DOI:10.1038/NATURE12373", "10.1038/nature12373"),
        # Uppercase DOI
        ("10.1038/NATURE12373", "10.1038/nature12373"),
        # Whitespace trimming
        ("  10.1038/x/y  ", "10.1038/x/y"),
        ("\t10.1038/x/y\n", "10.1038/x/y"),
        # Prefix with whitespace after
        ("doi: 10.1038/nature12373", "10.1038/nature12373"),
        ("https://doi.org/ 10.1038/nature12373", "10.1038/nature12373"),
        # Longer DOI
        ("10.1016/j.neuron.2020.01.029", "10.1016/j.neuron.2020.01.029"),
    ],
)
def test_normalize_doi_valid_cases(input_value, expected):
    assert normalize_doi(input_value) == expected


@pytest.mark.parametrize(
    "input_value",
    [
        None,
        "",
        "   ",
        "\t\n",
        "not-a-doi",
        "abc/def",  # doesn't start with 10.
        "10",  # no slash
        "10.",  # no slash
        "10.1234",  # no slash
        "10abc/def",  # doesn't start with 10.
        "20.1038/nature",  # wrong prefix
        "10/abc",  # has 10/ but not 10.
        12345,  # not a string
        [],  # not a string
    ],
)
def test_normalize_doi_invalid_cases(input_value):
    assert normalize_doi(input_value) is None


@pytest.mark.parametrize(
    "input_value,expected_valid",
    [
        ("10.1038/nature12373", True),
        ("https://doi.org/10.1038/nature12373", True),
        ("doi:10.1038/nature", True),
        ("", False),
        (None, False),
        ("not-a-doi", False),
        ("abc/def", False),
    ],
)
def test_is_valid_doi(input_value, expected_valid):
    assert is_valid_doi(input_value) == expected_valid
