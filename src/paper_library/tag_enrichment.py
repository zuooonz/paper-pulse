"""Deterministic normalization for structured paper filters.

The functions in this module intentionally use existing summaries and structured
metadata only.  They do not call an LLM and never require the extracted paper
full text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from paper_library.json_utils import decode_json
from core.taxonomy_config import (
    dimension_aliases,
    dimension_groups,
    dimension_patterns,
    load_taxonomy_config,
)


_TAXONOMY = load_taxonomy_config()
DISEASE_PATTERNS = dimension_patterns(_TAXONOMY, "diseases")
DISEASE_TAG_ALIASES = dimension_aliases(_TAXONOMY, "diseases")
STUDY_DESIGN_PATTERNS = dimension_patterns(_TAXONOMY, "study_design")
PARADIGM_ALIASES = dimension_aliases(_TAXONOMY, "paradigms")
PARADIGM_GROUPS = dimension_groups(_TAXONOMY, "paradigms")
RESEARCH_TOPIC_ALIASES = dimension_aliases(_TAXONOMY, "research_topics")
NEUROSCIENCE_SUBTOPICS = tuple(
    str(item["label"]) for item in (_TAXONOMY.get("dimensions", {}).get("neuroscience_subtopics", {}).get("items", []))
    if isinstance(item, dict) and item.get("label")
)
MULTIMODAL_ALIASES = dimension_aliases(_TAXONOMY, "multimodal")
RESEARCH_TOPIC_GROUPS = dimension_groups(_TAXONOMY, "research_topics")
DISEASE_GROUPS = dimension_groups(_TAXONOMY, "diseases")
STUDY_DESIGN_GROUPS = dimension_groups(_TAXONOMY, "study_design")
MULTIMODAL_METHOD_GROUPS = dimension_groups(_TAXONOMY, "multimodal")
INVALID_CUSTOM_PARADIGMS = {"/", "-", "--", "none", "null", "无", "未填写", "未判断"}


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        parsed = decode_json(value, value)
        return value if parsed == value else _as_text(parsed)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    if isinstance(value, Iterable):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _first_match(text: str, catalog: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    for label, patterns in catalog:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return label
    return None


_SUBJECT_CONTEXT_RE = re.compile(
    r"患者|病例|受试者|被试|参与者|人群|对照|儿童|成人|老年人|"
    r"patients?|cases?|participants?|subjects?|individuals?|people|adults?|children|"
    r"diagnos(?:ed|is)|suffer(?:ing|ed)?|persons?\s+with|people\s+with",
    re.IGNORECASE,
)


def _matching_labels(
    text: str,
    catalog: tuple[tuple[str, tuple[str, ...]], ...] = DISEASE_PATTERNS,
) -> set[str]:
    return {
        label
        for label, patterns in catalog
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
    }


def _has_subject_context(text: str, patterns: tuple[str, ...]) -> bool:
    """Reject isolated abstract mentions such as outcome lists or exclusions."""
    matches = [
        match
        for pattern in patterns
        for match in re.finditer(pattern, text, re.IGNORECASE)
    ]
    if len(matches) >= 2:
        return True
    return any(
        _SUBJECT_CONTEXT_RE.search(text[max(0, match.start() - 140): match.end() + 140])
        for match in matches
    )


def _tag_supported_labels(tags: Any) -> set[str]:
    values = {
        item.strip().casefold()
        for item in (tags if isinstance(tags, list) else [])
        if isinstance(item, str) and item.strip()
    }
    return {
        label
        for label, aliases in DISEASE_TAG_ALIASES.items()
        if any(alias.casefold() in values for alias in aliases)
    }


def classify_subject_diseases(
    population: Any,
    abstract: str | None = None,
    *,
    title: str | None = None,
    title_zh: str | None = None,
    abstract_zh: str | None = None,
    keywords: Any = None,
    keywords_zh: Any = None,
    tags: Any = None,
) -> dict[str, list[str]]:
    """Return all evidence-backed disease/group labels and their sources.

    Existing tags are supporting evidence only: a tag without any matching
    population/title/abstract/keyword text is deliberately not copied into the
    structured classification.
    """
    strong_sources = {
        "population": _as_text(population),
        "title": title or "",
        "title_zh": title_zh or "",
        "keywords": _as_text(keywords),
        "keywords_zh": _as_text(keywords_zh),
    }
    abstract_sources = {
        "abstract": abstract or "",
        "abstract_zh": abstract_zh or "",
    }
    tag_labels = _tag_supported_labels(tags)
    evidence: dict[str, set[str]] = {}

    for source, text_value in strong_sources.items():
        for label in _matching_labels(text_value):
            evidence.setdefault(label, set()).add(source)

    pattern_map = dict(DISEASE_PATTERNS)
    for source, text_value in abstract_sources.items():
        for label in _matching_labels(text_value):
            if label in tag_labels or _has_subject_context(text_value, pattern_map[label]):
                evidence.setdefault(label, set()).add(source)

    for label in tag_labels & evidence.keys():
        evidence[label].add("tag")

    return {
        label: sorted(evidence[label])
        for label, _ in DISEASE_PATTERNS
        if label in evidence
    }


def extract_subject_diseases(
    population: Any,
    abstract: str | None = None,
    **context: Any,
) -> list[str]:
    """Return stable, multi-value disease/group labels in catalog order."""
    return list(classify_subject_diseases(population, abstract, **context))


def extract_subject_disease(population: Any, abstract: str | None = None) -> str | None:
    """Return one canonical subject group, preferring curated population data."""
    population_text = _as_text(population)
    match = _first_match(population_text, DISEASE_PATTERNS)
    if match:
        return match
    return _first_match(abstract or "", DISEASE_PATTERNS)


_AGE_PATTERNS = (
    re.compile(r"(?:mean|average)\s+age\s*(?:was|of|[:=])?\s*(\d{1,3}(?:\.\d+)?)", re.I),
    re.compile(r"age\s*[,;(]?\s*(?:mean|average)\s*(?:±|\+/-|[:=])?\s*(\d{1,3}(?:\.\d+)?)", re.I),
    re.compile(r"平均年龄\s*(?:为|是|[:：=])?\s*(\d{1,3}(?:\.\d+)?)\s*岁?"),
)


def extract_subject_age_mean(abstract: str | None) -> float | None:
    """Extract only explicitly reported mean/average age from an abstract."""
    if not abstract:
        return None
    for pattern in _AGE_PATTERNS:
        match = pattern.search(abstract)
        if match:
            value = round(float(match.group(1)), 1)
            if 0 <= value <= 120:
                return value
    return None


def extract_study_design(title: str | None, abstract: str | None) -> str | None:
    return _first_match(f"{title or ''} {abstract or ''}", STUDY_DESIGN_PATTERNS)


def normalize_catalog_values(value: Any, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    """Map free-form structured values to a stable, deduplicated vocabulary."""
    text = _as_text(value)
    found = []
    for label, terms in aliases.items():
        if any(_contains_alias(text, term) for term in terms):
            found.append(label)
    return found


def _contains_alias(text: str, term: str) -> bool:
    """Match short Latin abbreviations as tokens rather than substrings."""
    if re.fullmatch(r"[A-Za-z0-9]+", term) and len(term) <= 5:
        return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I))
    return term.casefold() in text.casefold()


def normalize_paradigms(value: Any) -> list[str]:
    return normalize_catalog_values(value, PARADIGM_ALIASES)


def normalize_paradigm_selection(value: Any) -> list[str]:
    """Normalize known aliases while preserving meaningful custom paradigms.

    Unlike ``normalize_paradigms``, this function is intended for the editor
    and migrations.  Unknown non-placeholder values remain available as custom
    paradigms instead of being silently discarded.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parsed = decode_json(value, [value])
        values = parsed if isinstance(parsed, list) else [parsed]
    elif isinstance(value, Iterable) and not isinstance(value, dict):
        values = list(value)
    else:
        values = [value]

    normalized: list[str] = []
    for raw_value in values:
        if raw_value is None:
            continue
        item = re.sub(r"\s+", " ", str(raw_value)).strip()
        if not item or item.casefold() in INVALID_CUSTOM_PARADIGMS:
            continue
        matches = normalize_catalog_values(item, PARADIGM_ALIASES)
        candidates = matches or [item]
        for candidate in candidates:
            if candidate not in normalized:
                normalized.append(candidate)
    return normalized


def normalize_multimodal_techniques(value: Any) -> list[str]:
    if isinstance(value, str):
        value = decode_json(value, value)
    # Only the explicit techniques list is eligible for a multimodal filter.
    if isinstance(value, dict):
        value = value.get("techniques", [])
    elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        value = [item.get("techniques", []) for item in value]
    return normalize_catalog_values(value, MULTIMODAL_ALIASES)


def _alias_pattern(term: str) -> re.Pattern[str]:
    if re.fullmatch(r"[A-Za-z0-9]+", term) and len(term) <= 5:
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.I)
    return re.compile(re.escape(term), re.I)


def _catalog_has_context(
    text: str,
    terms: tuple[str, ...],
    context_pattern: re.Pattern[str] | None,
) -> bool:
    matches = [
        match
        for term in terms
        for match in _alias_pattern(term).finditer(text)
    ]
    if len(matches) >= 2:
        return True
    if context_pattern is None:
        return False
    return any(
        context_pattern.search(text[max(0, match.start() - 160): match.end() + 160])
        for match in matches
    )


def _classify_catalog(
    existing: Any,
    aliases: dict[str, tuple[str, ...]],
    *,
    title: str | None = None,
    title_zh: str | None = None,
    abstract: str | None = None,
    abstract_zh: str | None = None,
    keywords: Any = None,
    keywords_zh: Any = None,
    tags: Any = None,
    abstract_context: re.Pattern[str] | None = None,
    explicit_tag_only: bool = False,
) -> dict[str, list[str]]:
    evidence: dict[str, set[str]] = {}

    for label in normalize_catalog_values(existing, aliases):
        evidence.setdefault(label, set()).add("existing")

    strong_sources = {
        "title": title or "",
        "title_zh": title_zh or "",
        "keywords": _as_text(keywords),
        "keywords_zh": _as_text(keywords_zh),
    }
    for source, value in strong_sources.items():
        for label in normalize_catalog_values(value, aliases):
            evidence.setdefault(label, set()).add(source)

    tag_labels = set(normalize_catalog_values(tags, aliases))
    tag_text = _as_text(tags)
    for source, value in {"abstract": abstract or "", "abstract_zh": abstract_zh or ""}.items():
        for label in normalize_catalog_values(value, aliases):
            if label in tag_labels or _catalog_has_context(value, aliases[label], abstract_context):
                evidence.setdefault(label, set()).add(source)

    if explicit_tag_only and re.search(r"联用|同步|simultaneous|concurrent|[-–—]", tag_text, re.I):
        for label in tag_labels:
            evidence.setdefault(label, set()).add("tag")

    for label in tag_labels & evidence.keys():
        evidence[label].add("tag")

    return {
        label: sorted(evidence[label])
        for label in aliases
        if label in evidence
    }


_PARADIGM_CONTEXT_RE = re.compile(
    r"任务|范式|条件|试次|执行|完成|进行|训练|task|paradigm|condition|trial|"
    r"perform(?:ed|ing)?|underwent|during|training",
    re.I,
)
_MULTIMODAL_CONTEXT_RE = re.compile(
    r"联用|同步|结合|联合|同时|采集|记录|测量|监测|"
    r"simultaneous|concurrent|combined|coupled|integrat|record(?:ed|ing)?|"
    r"measur(?:ed|ing)?|monitor(?:ed|ing)?|acquir(?:ed|ing)?|using",
    re.I,
)


def classify_research_topics(
    research_fields: Any,
    *,
    title: str | None = None,
    title_zh: str | None = None,
    abstract: str | None = None,
    abstract_zh: str | None = None,
    keywords: Any = None,
    keywords_zh: Any = None,
    tags: Any = None,
) -> dict[str, list[str]]:
    return _classify_catalog(
        research_fields,
        RESEARCH_TOPIC_ALIASES,
        title=title,
        title_zh=title_zh,
        abstract=abstract,
        abstract_zh=abstract_zh,
        keywords=keywords,
        keywords_zh=keywords_zh,
        tags=tags,
    )


def classify_paradigms(
    paradigm: Any,
    *,
    title: str | None = None,
    title_zh: str | None = None,
    abstract: str | None = None,
    abstract_zh: str | None = None,
    keywords: Any = None,
    keywords_zh: Any = None,
    tags: Any = None,
) -> dict[str, list[str]]:
    return _classify_catalog(
        paradigm,
        PARADIGM_ALIASES,
        title=title,
        title_zh=title_zh,
        abstract=abstract,
        abstract_zh=abstract_zh,
        keywords=keywords,
        keywords_zh=keywords_zh,
        tags=tags,
        abstract_context=_PARADIGM_CONTEXT_RE,
    )


def classify_multimodal_methods(
    multimodal_techniques: Any,
    *,
    title: str | None = None,
    title_zh: str | None = None,
    abstract: str | None = None,
    abstract_zh: str | None = None,
    keywords: Any = None,
    keywords_zh: Any = None,
    tags: Any = None,
) -> dict[str, list[str]]:
    existing = normalize_multimodal_techniques(multimodal_techniques)
    return _classify_catalog(
        existing,
        MULTIMODAL_ALIASES,
        title=title,
        title_zh=title_zh,
        abstract=abstract,
        abstract_zh=abstract_zh,
        keywords=keywords,
        keywords_zh=keywords_zh,
        tags=tags,
        abstract_context=_MULTIMODAL_CONTEXT_RE,
        explicit_tag_only=True,
    )
