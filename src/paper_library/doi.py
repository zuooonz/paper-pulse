"""DOI normalization and validation utilities.

This module provides pure functions for normalizing and validating DOI strings
from various sources (CrossRef, manual entry, URL pasting).
"""

from typing import Optional


# Common DOI prefixes to strip (case-insensitive)
DOI_PREFIXES = [
    "https://doi.org/",
    "http://doi.org/",
    "doi:",
    "DOI:",
]


def normalize_doi(raw: Optional[str]) -> Optional[str]:
    """Normalize a DOI string from various input formats.

    Args:
        raw: Raw input DOI string, may be None, or have whitespace,
             or have common prefixes like https://doi.org/ or doi:.

    Returns:
        Normalized lowercased DOI string if it matches basic DOI format,
        otherwise None.

    Rules:
        - Return None if input is None/empty/non-string
        - Strip leading/trailing whitespace
        - Remove common prefixes (https://doi.org/, http://doi.org/, doi:, DOI:)
        - Convert to lowercase
        - Validate that result starts with 10. and contains a /
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None

    # Strip common prefixes case-insensitively
    lower_s = s.lower()
    for prefix in DOI_PREFIXES:
        if lower_s.startswith(prefix.lower()):
            s = s[len(prefix):]
            break

    s = s.strip()
    if not s:
        return None

    normalized = s.lower()

    # Basic DOI format validation: must start with 10. and contain at least one /
    if normalized.startswith("10.") and "/" in normalized:
        return normalized

    return None


def is_valid_doi(s: Optional[str]) -> bool:
    """Check if input is a valid DOI string after normalization.

    Args:
        s: Input DOI string to check.

    Returns:
        True if normalization produces a valid DOI, False otherwise.
    """
    return normalize_doi(s) is not None
