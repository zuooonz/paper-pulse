"""Conservative matching between verified author affiliations and map locations.

The author table stores a person's current/verified affiliation while
``affiliation_locations`` stores the canonical map entity.  These records must
not be joined by author name or pinyin.  This module only considers a location
when its institution name is explicitly contained in the author's affiliation
text, and reports ambiguous matches instead of guessing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_NON_WORD_RE = re.compile(r"[^0-9a-z\u3400-\u9fff]+")


@dataclass(frozen=True)
class LocationCandidate:
    id: int
    affiliation_zh: str | None
    canonical_name_en: str | None
    country: str | None
    province: str | None
    city: str | None
    lat: float | None
    lng: float | None
    trusted: bool = True


@dataclass(frozen=True)
class MatchResult:
    location_id: int | None
    status: str
    score: int = 0
    matched_field: str | None = None


def normalize_special_jurisdiction(
    affiliation: str | None,
    country: str | None,
    province: str | None,
    city: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Use institution jurisdiction for Hong Kong/Macau map filtering.

    Mainland map providers may label the University of Macau's Hengqin campus
    as Zhuhai even though it is administered by Macau.  Institution/province
    text is deterministic evidence for these special administrative regions.
    """
    evidence = " ".join(value or "" for value in (affiliation, province, city))
    folded = evidence.casefold()
    if "香港" in evidence or "hong kong" in folded:
        return "Hong Kong", "香港特别行政区", "香港"
    if "澳门" in evidence or "macau" in folded or "macao" in folded:
        return "Macau", "澳门特别行政区", "澳门"
    return country, province, city


def normalize_affiliation(value: str | None) -> str:
    """Normalize punctuation/case without translating or inferring entities."""
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    return _NON_WORD_RE.sub("", normalized)


def _field_score(author_text: str, candidate_text: str | None) -> int:
    candidate = normalize_affiliation(candidate_text)
    if not candidate:
        return 0
    has_han = bool(_HAN_RE.search(candidate))
    minimum = 6 if has_han else 12
    if len(candidate) < minimum or candidate not in author_text:
        return 0
    # Prefer the most specific institution/department record.
    return len(candidate) + (1000 if has_han else 500)


def candidate_score(
    affiliation_zh: str | None,
    affiliation_en: str | None,
    candidate: LocationCandidate,
) -> tuple[int, str | None]:
    """Return an explicit containment score, never a semantic similarity."""
    candidate_label = candidate.affiliation_zh or ""
    if _HAN_RE.search(candidate_label):
        zh_score = _field_score(
            normalize_affiliation(affiliation_zh), candidate_label,
        )
        label_field = "affiliation_zh"
    else:
        # Legacy rows sometimes store the publication's English affiliation in
        # affiliation_zh.  It remains source text, not a translated Chinese name.
        zh_score = _field_score(
            normalize_affiliation(affiliation_en), candidate_label,
        )
        label_field = "affiliation_en"
    en_score = _field_score(
        normalize_affiliation(affiliation_en), candidate.canonical_name_en,
    )
    if zh_score >= en_score and zh_score:
        return zh_score, label_field
    if en_score:
        return en_score, "affiliation_en"
    return 0, None


def choose_location(
    affiliation_zh: str | None,
    affiliation_en: str | None,
    candidates: list[LocationCandidate],
    current_location_id: int | None = None,
) -> MatchResult:
    """Choose one explicit, unique location or return a review status.

    A matching current location is retained to avoid needless relinking from a
    parent institution to a department-level duplicate.  Otherwise the most
    specific unique match wins.  Equal top scores are deliberately ambiguous.
    """
    scored: list[tuple[int, str, LocationCandidate]] = []
    for candidate in candidates:
        score, field = candidate_score(affiliation_zh, affiliation_en, candidate)
        if not score or field is None:
            continue
        if candidate.id == current_location_id:
            return MatchResult(candidate.id, "current_match", score, field)
        if not candidate.trusted:
            continue
        scored.append((score, field, candidate))

    if not scored:
        return MatchResult(None, "no_explicit_match")
    scored.sort(key=lambda item: (-item[0], item[2].id))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return MatchResult(None, "ambiguous_match", scored[0][0], scored[0][1])
    score, field, candidate = scored[0]
    return MatchResult(candidate.id, "relink", score, field)
