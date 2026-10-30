"""Conservative paper-local author reconciliation from authoritative sources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from paper_library.affiliation_normalizer import match_cn_to_en_fuzzy
from paper_library.author_identity import (
    add_author_identifier,
    contains_han,
    normalize_identifier,
    upsert_paper_author_mention,
)
from paper_library.models import (
    Author,
    AuthorIdentifier,
    AuthorIdentityEvent,
    Paper,
    PaperAuthorMention,
    paper_authors,
)


@dataclass(frozen=True)
class SourceAuthor:
    order: int
    name: str
    affiliation: str = ""
    email: str = ""
    orcid: str = ""
    source_type: str = "crossref_snapshot"
    source_ref: str = ""
    is_corresponding: bool = False


def normalize_english_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def english_name_token_key(value: str | None) -> tuple[str, ...]:
    return tuple(sorted(re.findall(r"[a-z0-9]+", (value or "").casefold())))


def _is_placeholder_name(value: str | None) -> bool:
    return (value or "").strip() in {"未注明", "未知", "不详"}


def looks_like_author_list(value: str | None) -> bool:
    if not value:
        return False
    return bool(re.search(r"\s(?:、|；|;|&|\band\b)\s|、|；|;", value, re.I))


def purge_reconciliation_orphans(session) -> dict:
    """Delete only orphan authors explicitly detached by this reconciler."""
    stale_ids: set[int] = set()
    events = session.scalars(select(AuthorIdentityEvent).where(
        AuthorIdentityEvent.event_type == "paper_source_reconcile"
    )).all()
    for event in events:
        try:
            evidence = json.loads(event.evidence or "{}")
        except json.JSONDecodeError:
            continue
        stale_ids.update(
            value for value in evidence.get("stale_author_ids", [])
            if isinstance(value, int)
        )
    deleted_ids = []
    for author_id in sorted(stale_ids):
        if session.get(Author, author_id) is None:
            continue
        link_count = session.scalar(select(func.count()).select_from(paper_authors).where(
            paper_authors.c.author_id == author_id
        ))
        mention_count = session.scalar(select(func.count()).select_from(PaperAuthorMention).where(
            PaperAuthorMention.author_id == author_id
        ))
        identifier_count = session.scalar(select(func.count()).select_from(AuthorIdentifier).where(
            AuthorIdentifier.author_id == author_id
        ))
        if link_count or mention_count or identifier_count:
            continue
        session.execute(delete(Author).where(Author.id == author_id))
        deleted_ids.append(author_id)
    return {"authors_deleted": len(deleted_ids), "author_ids": deleted_ids}


def _links(session, paper_id: int) -> list[dict]:
    rows = session.execute(
        select(
            Author.id.label("author_id"), Author.name.label("person_name"),
            paper_authors.c.author_order, paper_authors.c.is_first,
            paper_authors.c.is_corresponding, PaperAuthorMention.name_raw,
            PaperAuthorMention.name_zh, PaperAuthorMention.affiliation_raw,
            PaperAuthorMention.affiliation_zh,
        )
        .join(Author, Author.id == paper_authors.c.author_id)
        .outerjoin(PaperAuthorMention, (
            PaperAuthorMention.paper_id == paper_authors.c.paper_id
        ) & (
            PaperAuthorMention.author_id == paper_authors.c.author_id
        ))
        .where(paper_authors.c.paper_id == paper_id)
        .order_by(paper_authors.c.author_order, Author.id)
    ).mappings()
    return [dict(row) for row in rows]


def _publication_name(link: dict) -> str:
    return (link.get("name_raw") or link.get("person_name") or "").strip()


def _match_excel_fact(fact, authors: list[SourceAuthor]) -> SourceAuthor | None:
    name = (getattr(fact, "name", "") or "").strip()
    if not name:
        return None
    if contains_han(name):
        matches = [
            author for author in authors
            if match_cn_to_en_fuzzy(name, author.name)
        ]
    else:
        key = normalize_english_name(name)
        token_key = english_name_token_key(name)
        matches = [
            author for author in authors
            if normalize_english_name(author.name) == key
            or english_name_token_key(author.name) == token_key
        ]
    return matches[0] if len(matches) == 1 else None


def reconcile_combined_author_paper(
    session,
    *,
    paper: Paper,
    source_authors: list[SourceAuthor],
    excel_facts: list,
    actor: str = "reconcile_author_relations_from_sources",
    allow_clean_refresh: bool = False,
) -> dict:
    """Rebuild a proven combined or paper-local duplicate relation.

    This function deliberately refuses ordinary papers.  It repairs only an
    explicit multi-name relation or a stale Han-only relation that uniquely
    maps to an already represented publication author.
    """
    if not source_authors:
        raise ValueError("Authoritative English author list is empty")
    links = _links(session, paper.id)
    source_keys = [normalize_english_name(author.name) for author in source_authors]
    if not all(source_keys) or len(source_keys) != len(set(source_keys)):
        raise ValueError("Authoritative author names are missing or ambiguous")

    mapped: dict[int, dict] = {}
    used_link_ids: set[int] = set()
    for source in source_authors:
        orcid_person = None
        if source.orcid:
            normalized_orcid = normalize_identifier("orcid", source.orcid)
            identifier = session.scalar(select(AuthorIdentifier).where(
                AuthorIdentifier.identifier_type == "orcid",
                AuthorIdentifier.normalized_value == normalized_orcid,
            ))
            orcid_person = session.get(Author, identifier.author_id) if identifier else None
            if orcid_person is None:
                orcid_person = session.scalar(select(Author).where(
                    Author.orcid == normalized_orcid
                ))
            if orcid_person is not None and orcid_person.id in used_link_ids:
                raise ValueError(f"ORCID resolves to an already-used author: {normalized_orcid}")
        exact = [
            link for link in links
            if link["author_id"] not in used_link_ids
            and not contains_han(_publication_name(link))
            and not looks_like_author_list(_publication_name(link))
            and (
                normalize_english_name(_publication_name(link))
                == normalize_english_name(source.name)
                or english_name_token_key(_publication_name(link))
                == english_name_token_key(source.name)
            )
        ]
        candidates = exact
        if not candidates:
            candidates = [
                link for link in links
                if link["author_id"] not in used_link_ids
                and contains_han(_publication_name(link))
                and match_cn_to_en_fuzzy(_publication_name(link), source.name)
            ]
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous existing relation for {source.name}")
        if orcid_person is not None:
            existing_link = next(
                (item for item in links if item["author_id"] == orcid_person.id), None
            )
            if existing_link is not None:
                link = existing_link
            else:
                session.execute(paper_authors.insert().values(
                    paper_id=paper.id,
                    author_id=orcid_person.id,
                    author_order=source.order,
                    is_first=False,
                    is_corresponding=False,
                ))
                link = {
                    "author_id": orcid_person.id,
                    "person_name": orcid_person.name,
                    "name_raw": None,
                    "name_zh": None,
                    "affiliation_raw": None,
                    "affiliation_zh": None,
                }
            used_link_ids.add(orcid_person.id)
        elif candidates:
            link = candidates[0]
            used_link_ids.add(link["author_id"])
        else:
            person = None
            if person is None:
                person = Author(name=source.name)
                session.add(person)
                session.flush()
            existing_link = next(
                (item for item in links if item["author_id"] == person.id), None
            )
            if existing_link is not None:
                link = existing_link
            else:
                session.execute(paper_authors.insert().values(
                    paper_id=paper.id,
                    author_id=person.id,
                    author_order=source.order,
                    is_first=False,
                    is_corresponding=False,
                ))
                link = {
                    "author_id": person.id,
                    "person_name": source.name,
                    "name_raw": None,
                    "name_zh": None,
                    "affiliation_raw": None,
                    "affiliation_zh": None,
                }
            used_link_ids.add(person.id)
        mapped[source.order] = link

    # Reuse only Chinese aliases already recorded against the same canonical
    # author on another paper. This resolves non-Mandarin romanizations
    # without translating names or merging people on English-name similarity.
    aliases_by_order: dict[int, set[str]] = {}
    for order, link in mapped.items():
        aliases_by_order[order] = {
            value.strip() for value in session.scalars(
                select(PaperAuthorMention.name_zh).where(
                    PaperAuthorMention.author_id == link["author_id"],
                    PaperAuthorMention.paper_id != paper.id,
                    PaperAuthorMention.name_zh.is_not(None),
                )
            )
            if value and contains_han(value)
        }

    facts_by_order: dict[int, list] = {}
    unmatched_facts = []
    for fact in excel_facts:
        if _is_placeholder_name(getattr(fact, "name", "")):
            continue
        matched = _match_excel_fact(fact, source_authors)
        if matched is None and contains_han(getattr(fact, "name", "")):
            alias_orders = [
                order for order, aliases in aliases_by_order.items()
                if getattr(fact, "name", "").strip() in aliases
            ]
            if len(alias_orders) == 1:
                matched = next(
                    author for author in source_authors
                    if author.order == alias_orders[0]
                )
        if matched is not None:
            facts_by_order.setdefault(matched.order, []).append(fact)
        else:
            unmatched_facts.append(fact)

    # Excel's first-author slot is authoritative even when the spelling is
    # reversed or damaged, because publication metadata defines order zero.
    still_unmatched = []
    for fact in unmatched_facts:
        if getattr(fact, "role", "") == "first" and getattr(fact, "slot", 0) == 0:
            facts_by_order.setdefault(0, []).append(fact)
        else:
            still_unmatched.append(fact)

    # Full-text correspondence markers resolve uncommon romanizations and
    # publisher typos only when exactly one corresponding author remains.
    unmatched_facts = []
    assigned_corresponding = {
        order for order, items in facts_by_order.items()
        if any(getattr(fact, "role", "") == "corresponding" for fact in items)
    }
    for fact in still_unmatched:
        if getattr(fact, "role", "") != "corresponding":
            unmatched_facts.append(fact)
            continue
        candidates = [
            author for author in source_authors
            if author.is_corresponding and author.order not in assigned_corresponding
        ]
        if len(candidates) == 1:
            facts_by_order.setdefault(candidates[0].order, []).append(fact)
            assigned_corresponding.add(candidates[0].order)
        else:
            unmatched_facts.append(fact)

    # Last conservative fallback: after all other Excel role facts are mapped,
    # one remaining corresponding fact may map to one remaining source author.
    assigned_any = set(facts_by_order)
    for fact in unmatched_facts:
        if getattr(fact, "role", "") != "corresponding":
            continue
        candidates = [
            author for author in source_authors if author.order not in assigned_any
        ]
        if len(candidates) == 1:
            facts_by_order.setdefault(candidates[0].order, []).append(fact)
            assigned_any.add(candidates[0].order)

    has_excel_first = any(
        getattr(fact, "role", "") in {"first", "co_first"}
        for facts in facts_by_order.values() for fact in facts
    )
    corresponding_orders = {
        order for order, facts in facts_by_order.items()
        if any(getattr(fact, "role", "") == "corresponding" for fact in facts)
    }
    first_orders = {
        order for order, facts in facts_by_order.items()
        if any(getattr(fact, "role", "") in {"first", "co_first"} for fact in facts)
    }
    if not has_excel_first:
        first_orders = {0}
    source_corresponding_orders = {
        author.order for author in source_authors if author.is_corresponding
    }
    # Explicit publisher/full-text correspondence markers are authoritative.
    # Excel role columns remain a fallback for sources without such metadata,
    # but must not preserve an erroneous "last author = corresponding" guess.
    if source_corresponding_orders:
        corresponding_orders = source_corresponding_orders

    kept_ids = {link["author_id"] for link in mapped.values()}
    original_ids = {link["author_id"] for link in links}
    stale_ids = {link["author_id"] for link in links} - kept_ids
    stale_links = [link for link in links if link["author_id"] in stale_ids]
    has_combined = any(looks_like_author_list(_publication_name(link)) for link in links)
    has_placeholder_stale = any(
        _is_placeholder_name(_publication_name(link)) for link in stale_links
    )
    has_han_mapped = any(
        link["author_id"] in kept_ids and contains_han(_publication_name(link))
        for link in links
    )
    safe_duplicate_stale = bool(stale_links) and all(
        contains_han(_publication_name(link))
        and len([
            author for author in source_authors
            if match_cn_to_en_fuzzy(_publication_name(link), author.name)
        ]) == 1
        for link in stale_links
    )
    has_excel_duplicate_stale = any(
        contains_han(_publication_name(link))
        and mapped[order]["author_id"] != link["author_id"]
        and any(
            (getattr(fact, "name", "") or "").strip()
            == _publication_name(link)
            for fact in facts
        )
        for link in stale_links
        for order, facts in facts_by_order.items()
    )
    source_lists_agree = bool(source_authors) and all(
        "api.crossref.org" in source.source_ref
        and "europepmc.org" in source.source_ref
        for source in source_authors
    )
    unmatched_excel_han_names = {
        getattr(fact, "name", "").strip() for fact in unmatched_facts
        if contains_han(getattr(fact, "name", ""))
    }
    has_source_excluded_stale = source_lists_agree and any(
        _publication_name(link) in unmatched_excel_han_names
        for link in stale_links
    )
    safe_stale = all(
        looks_like_author_list(_publication_name(link))
        or _publication_name(link).strip() in {"未注明", "未知", "不详"}
        or (
            contains_han(_publication_name(link))
            and len([
                author for author in source_authors
                if match_cn_to_en_fuzzy(_publication_name(link), author.name)
            ]) == 1
        )
        or any(
            source.orcid
            and normalize_english_name(_publication_name(link))
            == normalize_english_name(source.name)
            and mapped[source.order]["author_id"] != link["author_id"]
            for source in source_authors
        )
        or any(
            contains_han(_publication_name(link))
            and mapped[order]["author_id"] != link["author_id"]
            and any(
                (getattr(fact, "name", "") or "").strip()
                == _publication_name(link)
                for fact in facts
            )
            for order, facts in facts_by_order.items()
        )
        or (
            source_lists_agree
            and _publication_name(link) in unmatched_excel_han_names
        )
        for link in stale_links
    )
    clean_refresh = (
        allow_clean_refresh
        and not stale_ids
        and kept_ids == original_ids
        and len(kept_ids) == len(source_authors)
    )
    if (
        not (
            has_combined or has_han_mapped or safe_duplicate_stale
            or has_placeholder_stale or has_excel_duplicate_stale
            or has_source_excluded_stale
        )
        or not safe_stale
    ) and not clean_refresh:
        raise ValueError("Paper has no proven combined or unique duplicate relation")
    if stale_ids:
        session.execute(delete(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == paper.id,
            PaperAuthorMention.author_id.in_(stale_ids),
        ))
        session.execute(paper_authors.delete().where(
            paper_authors.c.paper_id == paper.id,
            paper_authors.c.author_id.in_(stale_ids),
        ))

    for source in source_authors:
        link = mapped[source.order]
        facts = facts_by_order.get(source.order, [])
        chinese_names = [
            getattr(fact, "name", "").strip() for fact in facts
            if contains_han(getattr(fact, "name", ""))
        ]
        # A role-specific workbook affiliation is safer than another role's
        # value for an author who is both first and corresponding.  Multiple
        # conflicting values within the selected role are never resolved by
        # column order: leave the Chinese affiliation empty for review.
        preferred_affiliation_facts = [
            fact for fact in facts
            if (
                source.order in corresponding_orders
                and getattr(fact, "role", "") == "corresponding"
            ) or (
                source.order not in corresponding_orders
                and source.order in first_orders
                and getattr(fact, "role", "") in {"first", "co_first"}
            )
        ]
        affiliation_facts = preferred_affiliation_facts or facts
        chinese_affiliations = [
            getattr(fact, "affiliation", "").strip()
            for fact in affiliation_facts
            if contains_han(getattr(fact, "affiliation", ""))
        ]
        name_zh = chinese_names[0] if len(set(chinese_names)) == 1 else ""
        unique_chinese_affiliations = list(dict.fromkeys(chinese_affiliations))
        affiliation_zh = (
            unique_chinese_affiliations[0]
            if len(unique_chinese_affiliations) == 1 else ""
        )
        source_refs = [source.source_ref] + [
            getattr(fact, "source_ref", "") for fact in facts
            if getattr(fact, "source_ref", "")
        ]
        session.execute(paper_authors.update().where(
            paper_authors.c.paper_id == paper.id,
            paper_authors.c.author_id == link["author_id"],
        ).values(
            author_order=source.order,
            is_first=source.order in first_orders,
            is_corresponding=source.order in corresponding_orders,
        ))
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=link["author_id"],
            name_raw=source.name,
            name_zh=name_zh or None,
            affiliation_raw=source.affiliation or None,
            affiliation_zh=affiliation_zh or None,
            email_raw=source.email or None,
            orcid_raw=source.orcid or None,
            source_type="source_reconciled",
            source_ref=" | ".join(dict.fromkeys(ref for ref in source_refs if ref)),
            verification_status="source_recorded",
            evidence_text=(
                "English name/affiliation from publication metadata; Chinese "
                "name/affiliation only from the scoped workbook row."
            ),
            replace_empty=True,
        )

        paper_count = session.scalar(select(func.count()).select_from(paper_authors).where(
            paper_authors.c.author_id == link["author_id"]
        ))
        if paper_count == 1:
            person = session.get(Author, link["author_id"])
            person.name = source.name
            person.name_zh = name_zh or None
            person.affiliation = source.affiliation or None
            person.affiliation_zh = affiliation_zh or None
            person.email = source.email or None
            person.orcid = normalize_identifier("orcid", source.orcid) if source.orcid else None
        for kind, value in (("email", source.email), ("orcid", source.orcid)):
            if value:
                add_author_identifier(
                    session,
                    author_id=link["author_id"],
                    identifier_type=kind,
                    identifier_value=value,
                    source_type=source.source_type,
                    source_paper_id=paper.id,
                    verification_status="source_recorded",
                )

    session.add(AuthorIdentityEvent(
        event_type="paper_source_reconcile",
        paper_id=paper.id,
        reason="Reconciled proven combined or duplicate author relations from authoritative sources",
        evidence=json.dumps({
            "internal_id": paper.internal_id,
            "doi": paper.doi,
            "author_count": len(source_authors),
            "stale_author_ids": sorted(stale_ids),
            "kept_author_ids": sorted(kept_ids),
        }, ensure_ascii=False),
        actor=actor,
    ))
    return {
        "internal_id": paper.internal_id,
        "authors_reconciled": len(source_authors),
        "relations_removed": len(stale_ids),
        "relations_created": len(kept_ids - {link["author_id"] for link in links}),
    }
