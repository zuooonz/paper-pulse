"""Create canonical author identities and publication-specific snapshots."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict

from sqlalchemy import and_, select

from paper_library.author_identity import upsert_paper_author_mention
from paper_library.models import Author, Paper, PaperAuthorMention, paper_authors
from paper_library.paper_lifecycle import refresh_metadata_complete


def _linked_author_for_paper(
    session,
    *,
    paper_id: int,
    name: str,
    affiliation: str,
    orcid: str | None,
    author_order: int,
) -> Author | None:
    """Reuse a matching relation already on this paper, including ambiguous names."""
    rows = session.execute(
        select(
            Author,
            paper_authors.c.author_order,
            PaperAuthorMention.affiliation_raw,
            PaperAuthorMention.orcid_raw,
            PaperAuthorMention.name_raw,
        )
        .join(paper_authors, paper_authors.c.author_id == Author.id)
        .outerjoin(
            PaperAuthorMention,
            and_(
                PaperAuthorMention.paper_id == paper_authors.c.paper_id,
                PaperAuthorMention.author_id == paper_authors.c.author_id,
            ),
        )
        .where(
            paper_authors.c.paper_id == paper_id,
            (Author.name == name) | (PaperAuthorMention.name_raw == name),
        )
        .order_by(paper_authors.c.author_order)
    ).all()
    if not rows:
        return None

    if orcid:
        matching = [
            row for row in rows
            if (row[0].orcid or "").replace("https://orcid.org/", "").strip() == orcid
            or (row[3] or "").replace("https://orcid.org/", "").strip() == orcid
        ]
        return matching[0][0] if matching else None

    if affiliation:
        matching = [
            row for row in rows
            if row[2] == affiliation or row[0].affiliation == affiliation
        ]
        if matching:
            return matching[0][0]

    # The former implementation reused only a unique no-affiliation name match.
    # If this paper already has several such rows, author_order makes retries stable.
    if len(rows) == 1:
        return rows[0][0]
    same_order = [row for row in rows if row[1] == author_order]
    if same_order:
        return same_order[0][0]
    return rows[0][0]


def normalize_authors_for_paper(
    doi_meta: dict,
    paper_id: int,
    session,
    *,
    source_type: str = "crossref_snapshot",
    source_ref: str | None = None,
) -> int:
    """Extract authors from Crossref metadata and link them to a paper.

    Authors are deduplicated into the shared ``authors`` table.  The relation and
    the paper-specific Crossref snapshot are upserted, so the function can be
    called repeatedly.  Returns the number of authors processed.
    """
    msg = doi_meta.get("message") if "message" in doi_meta else doi_meta
    author_list = msg.get("author", []) if isinstance(msg, dict) else []
    if not author_list:
        return 0

    paper = session.get(Paper, paper_id)
    source_ref = source_ref or (f"doi:{paper.doi}" if paper and paper.doi else None)

    # ORCID identities must not collapse into the name cache.
    author_cache: Dict[str, int] = {}
    count = 0

    for idx, a in enumerate(author_list):
        if not isinstance(a, dict):
            continue
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip() if given else family
        if not name:
            name = a.get("name", "")
        if not name:
            continue

        orcid_raw = a.get("ORCID", "")
        orcid = None
        if orcid_raw:
            orcid = orcid_raw.replace("https://orcid.org/", "").strip()

        aff_list = a.get("affiliation", [])
        affiliation = ""
        if isinstance(aff_list, list) and aff_list:
            affiliation = aff_list[0].get("name", "") if isinstance(aff_list[0], dict) else ""

        cache_key = f"orcid:{orcid}" if orcid else f"name:{name.lower()}"

        # L1: ORCID match
        if orcid and cache_key not in author_cache:
            existing = session.query(Author).filter(
                Author.orcid.in_([orcid, f"https://orcid.org/{orcid}"])
            ).first()
            if existing:
                author_cache[cache_key] = existing.id
                # Update name/affiliation
                existing.name = name
                existing.affiliation = affiliation or existing.affiliation

        # L2: name + affiliation match
        # An ORCID is authoritative: never associate it with a different ORCID
        # (or an unverified same-name record).
        if not orcid and cache_key not in author_cache and affiliation:
            existing = session.query(Author).filter(
                Author.name == name, Author.affiliation == affiliation
            ).first()
            if existing:
                author_cache[cache_key] = existing.id

        # L3: name match (only if affiliation is similar or not provided on either side)
        if not orcid and cache_key not in author_cache and affiliation:
            candidates = session.query(Author).filter(Author.name == name).all()
            if len(candidates) == 1:
                c = candidates[0]
                # Only match if existing author has no affiliation, or this one has no affiliation, or they're similar
                if not c.affiliation or not affiliation or \
                   SequenceMatcher(None, affiliation, c.affiliation or "").ratio() > 0.7:
                    author_cache[cache_key] = c.id
            elif len(candidates) > 1:
                best, best_sim = None, 0.0
                for c in candidates:
                    sim = SequenceMatcher(None, affiliation, c.affiliation or "").ratio()
                    if sim > best_sim:
                        best_sim = sim
                        best = c
                if best and best_sim > 0.7:
                    author_cache[cache_key] = best.id

        # Crossref 经常不给 affiliation；此时仅在同名记录唯一时安全复用。
        if not orcid and cache_key not in author_cache and not affiliation:
            candidates = session.query(Author).filter(Author.name == name).all()
            if len(candidates) == 1:
                author_cache[cache_key] = candidates[0].id

        # Prefer an existing paper-specific relation when the global name is ambiguous.
        if cache_key not in author_cache:
            linked = _linked_author_for_paper(
                session,
                paper_id=paper_id,
                name=name,
                affiliation=affiliation,
                orcid=orcid,
                author_order=idx,
            )
            if linked:
                author_cache[cache_key] = linked.id

        # Create new author if not found
        if cache_key not in author_cache:
            new_a = Author(name=name, affiliation=affiliation or None, orcid=orcid)
            session.add(new_a)
            session.flush()
            author_cache[cache_key] = new_a.id

        author_id = author_cache[cache_key]

        # Create paper_authors link
        is_first = a.get("sequence") == "first"
        # Crossref 通常不可靠标注通讯作者；仅接受明确的 corresponding=true。
        is_corresponding = a.get("corresponding") is True
        existing_link = session.execute(
            select(paper_authors).where(
                paper_authors.c.paper_id == paper_id,
                paper_authors.c.author_id == author_id,
            )
        ).first()
        if existing_link:
            session.execute(
                paper_authors.update().where(
                    paper_authors.c.paper_id == paper_id,
                    paper_authors.c.author_id == author_id,
                ).values(
                    author_order=min(idx, existing_link.author_order),
                    is_first=bool(existing_link.is_first or is_first),
                    is_corresponding=bool(existing_link.is_corresponding or is_corresponding),
                )
            )
        else:
            session.execute(
                paper_authors.insert().values(
                    paper_id=paper_id,
                    author_id=author_id,
                    author_order=idx,
                    is_first=is_first,
                    is_corresponding=is_corresponding,
                )
            )

        upsert_paper_author_mention(
            session,
            paper_id=paper_id,
            author_id=author_id,
            name_raw=name,
            affiliation_raw=affiliation or None,
            orcid_raw=orcid,
            source_type=source_type,
            source_ref=source_ref,
            verification_status="source_recorded",
        )
        count += 1

    session.flush()
    if paper is not None:
        session.expire(paper, ["authors"])
        refresh_metadata_complete(paper)
    return count


def normalize_author_names_for_paper(author_names: str | list[str], paper_id: int, session) -> int:
    """Ingest a simple ordered author list from an Excel source."""
    if isinstance(author_names, str):
        names = re.split(r"\s*(?:;|；|\||\n|、|\band\b)\s*", author_names, flags=re.IGNORECASE)
    else:
        names = author_names
    records = [
        {"name": str(name).strip(), "sequence": "first" if index == 0 else "additional"}
        for index, name in enumerate(names)
        if str(name).strip()
    ]
    if not records:
        return 0
    return normalize_authors_for_paper(
        {"author": records},
        paper_id,
        session,
        source_type="excel_snapshot",
    )
