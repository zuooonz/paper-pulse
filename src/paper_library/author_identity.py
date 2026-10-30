"""Deterministic helpers for author identity evidence and paper snapshots."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from paper_library.models import Author, AuthorIdentifier, PaperAuthorMention


_ORCID_PATTERN = re.compile(r"^(?:https?://orcid\.org/)?([0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X])$", re.I)

_SOURCE_PRIORITY = {
    "relation_snapshot": 0,
    "history_snapshot": 5,
    "crossref_snapshot": 10,
    "pubmed_snapshot": 15,
    "source_reconciled": 20,
    "excel_source": 20,
    "manual_edit": 30,
    "manual_verified": 40,
}

_VERIFICATION_PRIORITY = {
    "unverified": 0,
    "source_recorded": 10,
    "manual": 20,
    "verified": 30,
}


def contains_han(value: str | None) -> bool:
    return bool(value and re.search(r"[\u3400-\u9fff]", value))


def normalize_identifier(identifier_type: str, value: str) -> str:
    """Normalize an identifier without making an identity decision."""
    raw = value.strip()
    kind = identifier_type.strip().lower()
    if kind == "email":
        return raw.casefold()
    if kind == "orcid":
        compact = raw.replace(" ", "")
        match = _ORCID_PATTERN.match(compact)
        if not match:
            raise ValueError(f"Invalid ORCID: {value}")
        normalized = match.group(1).upper()
        digits = normalized.replace("-", "")
        total = 0
        for char in digits[:-1]:
            total = (total + int(char)) * 2
        remainder = (12 - total % 11) % 11
        checksum = "X" if remainder == 10 else str(remainder)
        if digits[-1] != checksum:
            raise ValueError(f"Invalid ORCID checksum: {value}")
        return normalized
    return raw.casefold()


def add_author_identifier(
    session,
    *,
    author_id: int,
    identifier_type: str,
    identifier_value: str,
    source_type: str,
    source_paper_id: int | None = None,
    verification_status: str = "unverified",
) -> AuthorIdentifier:
    normalized = normalize_identifier(identifier_type, identifier_value)
    kind = identifier_type.strip().lower()
    if kind == "orcid":
        conflicting_identifier = session.scalar(select(AuthorIdentifier).where(
            AuthorIdentifier.identifier_type == "orcid",
            AuthorIdentifier.normalized_value == normalized,
            AuthorIdentifier.author_id != author_id,
        ))
        conflicting_author = session.scalar(select(Author).where(
            Author.orcid == normalized,
            Author.id != author_id,
        ))
        if conflicting_identifier or conflicting_author:
            raise ValueError(f"ORCID already belongs to another author: {normalized}")
    existing = session.scalar(select(AuthorIdentifier).where(
        AuthorIdentifier.author_id == author_id,
        AuthorIdentifier.identifier_type == kind,
        AuthorIdentifier.normalized_value == normalized,
    ))
    if existing:
        if verification_status == "verified" and existing.verification_status != "verified":
            existing.verification_status = "verified"
            existing.verified_at = datetime.now(timezone.utc)
        return existing
    row = AuthorIdentifier(
        author_id=author_id,
        identifier_type=kind,
        identifier_value=identifier_value.strip(),
        normalized_value=normalized,
        source_type=source_type,
        source_paper_id=source_paper_id,
        verification_status=verification_status,
        verified_at=datetime.now(timezone.utc) if verification_status == "verified" else None,
    )
    session.add(row)
    return row


def upsert_paper_author_mention(
    session,
    *,
    paper_id: int,
    author_id: int,
    name_raw: str,
    name_zh: str | None = None,
    affiliation_raw: str | None = None,
    affiliation_zh: str | None = None,
    email_raw: str | None = None,
    orcid_raw: str | None = None,
    source_type: str,
    source_ref: str | None = None,
    verification_status: str = "unverified",
    evidence_text: str | None = None,
    verified_by: str | None = None,
    replace_empty: bool = False,
) -> PaperAuthorMention:
    row = session.scalar(select(PaperAuthorMention).where(
        PaperAuthorMention.paper_id == paper_id,
        PaperAuthorMention.author_id == author_id,
    ))
    if row is not None:
        current_rank = (
            _VERIFICATION_PRIORITY.get(row.verification_status, 0),
            _SOURCE_PRIORITY.get(row.source_type, 0),
        )
        incoming_rank = (
            _VERIFICATION_PRIORITY.get(verification_status, 0),
            _SOURCE_PRIORITY.get(source_type, 0),
        )
        if incoming_rank < current_rank:
            # A lower-priority source may still fill a field that the stronger
            # source did not provide (for example CrossRef English affiliation
            # plus Excel Chinese affiliation).  It must not replace existing
            # values or provenance metadata.
            fill_values = {
                "name_zh": (name_zh or "").strip() or None,
                "affiliation_raw": (affiliation_raw or "").strip() or None,
                "affiliation_zh": (affiliation_zh or "").strip() or None,
                "email_raw": (email_raw or "").strip() or None,
                "orcid_raw": (orcid_raw or "").strip() or None,
            }
            for field, value in fill_values.items():
                if value is not None and not getattr(row, field):
                    setattr(row, field, value)
            return row
    values: dict[str, Any] = {
        "name_raw": name_raw.strip(),
        "name_zh": (name_zh or "").strip() or None,
        "affiliation_raw": (affiliation_raw or "").strip() or None,
        "affiliation_zh": (affiliation_zh or "").strip() or None,
        "email_raw": (email_raw or "").strip() or None,
        "orcid_raw": (orcid_raw or "").strip() or None,
        "source_type": source_type,
        "source_ref": source_ref,
        "verification_status": verification_status,
        "evidence_text": evidence_text,
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc) if verification_status == "verified" else None,
    }
    if row is None:
        row = PaperAuthorMention(paper_id=paper_id, author_id=author_id, **values)
        session.add(row)
    else:
        for field, value in values.items():
            if (
                not replace_empty
                and field in {"name_zh", "affiliation_raw", "affiliation_zh", "email_raw", "orcid_raw"}
                and value is None
                and getattr(row, field)
            ):
                continue
            setattr(row, field, value)
    return row
