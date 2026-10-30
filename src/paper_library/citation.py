"""Deterministic formatting helpers for APA citations."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any

from paper_library.author_identity import contains_han


_CHINESE_ANNOTATION = re.compile(r"(?:《[^》]*[\u3400-\u9fff][^》]*》|（[^）]*[\u3400-\u9fff][^）]*）)")
_PREPRINT_LABELS = {"预印本", "預印本"}
_SCP_TAG = re.compile(r"</?\s*scp\b[^>]*>", re.IGNORECASE)
_DASHES = "-‐‑‒–—"


def sanitize_scholarly_markup(value: str | None) -> str:
    """Remove Crossref/JATS small-caps markup while preserving its text.

    Publishers may expose technical acronyms inside ``<scp>`` tags.
    Crossref may also indent those tags across lines, so whitespace and common
    punctuation boundaries are normalized after removing the markup.
    """
    text = html.unescape(str(value or ""))
    text = _SCP_TAG.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    text = re.sub(rf"\s*([{re.escape(_DASHES)}])\s*", r"\1", text)
    return text


def sanitize_apa_journal_name(value: str | None) -> str:
    """Return a display-safe English journal name for an APA citation.

    Import annotations are sometimes stored in ``journal_name`` (for example
    ``（会议集）``).  They are useful operational notes, but are not part of the
    publication title and must not leak into the English APA output.
    """
    name = sanitize_scholarly_markup(value)
    if name in _PREPRINT_LABELS:
        return "Preprint"
    name = _CHINESE_ANNOTATION.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" ,;，；")
    return "" if contains_han(name) else name


def format_apa_author_list(authors: Iterable[str]) -> str:
    """Format already-normalized APA author names without duplicate periods."""
    names = [str(name).strip() for name in authors if name and not contains_han(str(name))]
    if not names:
        return ""
    if len(names) == 1:
        result = names[0]
    elif len(names) == 2:
        result = f"{names[0]}, & {names[1]}"
    elif len(names) <= 20:
        result = f"{', '.join(names[:-1])}, & {names[-1]}"
    else:
        result = f"{', '.join(names[:19])}, ... {names[-1]}"
    return result if result.endswith(".") else f"{result}."


def format_apa_person_name(value: str | None) -> str:
    """Normalize one English display name while preserving existing APA names."""
    name = re.sub(r"\s+", " ", str(value or "")).strip()
    if not name or contains_han(name):
        return ""
    # Reconciled legacy snapshots may already contain ``Family, I.``.
    if "," in name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    surname = parts[-1]
    initials = ". ".join(part[0].upper() for part in parts[:-1] if part) + "."
    return f"{surname}, {initials}"


def extract_crossref_author_names(doi_meta: Any) -> list[str]:
    """Extract ordered English author names from stored Crossref metadata."""
    if not isinstance(doi_meta, dict):
        return []
    message = doi_meta.get("message") if isinstance(doi_meta.get("message"), dict) else doi_meta
    authors = message.get("author") or message.get("authors")
    if not isinstance(authors, list):
        return []
    result: list[str] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        given = str(author.get("given") or "").strip()
        family = str(author.get("family") or "").strip()
        name = f"{given} {family}".strip() or str(author.get("name") or "").strip()
        if name and not contains_han(name):
            result.append(name)
    return result


def build_apa_reference(
    *,
    author_names: Iterable[str],
    year: int | str | None,
    title: str | None,
    journal: str | None,
    volume: str | None = None,
    issue: str | None = None,
    pages: str | None = None,
    doi: str | None = None,
) -> dict[str, Any]:
    """Build deterministic APA-style text plus Word-renderable segments.

    The structured segments let the Word renderer italicize the journal and
    volume without parsing or trusting model-generated bibliography text.
    """
    apa_authors = [format_apa_person_name(name) for name in author_names]
    author_text = format_apa_author_list(apa_authors)
    clean_title = sanitize_scholarly_markup(title) or "(No title)"
    clean_journal = sanitize_apa_journal_name(journal)
    clean_doi = str(doi or "").strip()
    if clean_doi.lower().startswith("https://doi.org/"):
        clean_doi = clean_doi[len("https://doi.org/"):]

    segments: list[dict[str, Any]] = []

    def add(text: str, *, italic: bool = False) -> None:
        if text:
            segments.append({"text": text, "italic": italic})

    if author_text:
        add(author_text + " ")
    if year:
        add(f"({year}). ")
    add(clean_title.rstrip(". ") + ". ")
    if clean_journal:
        add(clean_journal, italic=True)
    clean_volume = str(volume or "").strip()
    clean_issue = str(issue or "").strip()
    clean_pages = str(pages or "").strip()
    if clean_journal and (clean_volume or clean_pages):
        add(", ")
    if clean_volume:
        add(clean_volume, italic=True)
        if clean_issue:
            add(f"({clean_issue})")
        if clean_pages:
            add(f", {clean_pages}")
        add(". ")
    elif clean_pages:
        add(f"{clean_pages}. ")
    elif clean_journal:
        add(". ")
    if clean_doi:
        add(f"https://doi.org/{clean_doi}")

    text = "".join(str(segment["text"]) for segment in segments).strip()
    return {"text": text, "segments": segments}
