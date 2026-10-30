"""Enrich accepted_date and published_date via triple-source extraction:
- PubMed API
- Crossref metadata (existing doi_meta or fresh API)
- PDF full-text regex

Usage:
    python scripts/enrich_dates.py [--dry-run] [--limit N] [--force] [--paper-id ID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.date_extractor import (
    extract_accepted_from_pdf,
    extract_published_from_pdf,
    fetch_dates_from_pubmed,
    fetch_dates_from_pubmed_by_doi,
    extract_accepted_from_crossref_meta,
    extract_published_from_crossref_meta,
    _fetch_fresh_crossref,
    cross_check,
)


def process_paper(paper: Paper, force: bool = False) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Process a single paper. Returns {field: (source_tag, note)}."""
    result: dict[str, tuple[Optional[str], Optional[str]]] = {}

    for field, label in [("accepted", "Accepted"), ("published", "Published")]:
        db_date = getattr(paper, f"{field}_date")
        if not force and db_date is not None:
            result[field] = ("skip", "already_has_date")
            continue

        # --- Source 1: PubMed (cached per paper) ---
        if not hasattr(paper, '_pubmed_dates'):
            paper._pubmed_dates = None
            if paper.pmid:
                paper._pubmed_dates = fetch_dates_from_pubmed(str(paper.pmid))
            elif paper.doi:
                paper._pubmed_dates = fetch_dates_from_pubmed_by_doi(paper.doi)
        date_pubmed = paper._pubmed_dates.get(field) if paper._pubmed_dates else (None, "")

        # --- Source 2: Crossref (cached per paper) ---
        if not hasattr(paper, '_crossref_meta'):
            paper._crossref_meta = paper.doi_meta if (paper.doi_meta and isinstance(paper.doi_meta, dict)) else None
            if not paper._crossref_meta and paper.doi:
                paper._crossref_meta = _fetch_fresh_crossref(paper.doi)
        date_crossref = (None, "")
        if paper._crossref_meta:
            if field == "accepted":
                date_crossref = extract_accepted_from_crossref_meta(paper._crossref_meta)
            else:
                date_crossref = extract_published_from_crossref_meta(paper._crossref_meta)

        # --- Source 3: PDF ---
        date_pdf = (None, "")
        if paper.extracted_text:
            if field == "accepted":
                date_pdf = extract_accepted_from_pdf(paper.extracted_text)
            else:
                date_pdf = extract_published_from_pdf(paper.extracted_text)

        # --- Cross-check ---
        best_date, source = cross_check(date_pubmed, date_crossref, date_pdf)

        if best_date:
            setattr(paper, f"{field}_date", best_date)
            setattr(paper, f"{field}_date_source", source)
            result[field] = (source, None)
        else:
            result[field] = (None, "no_date_found")

    return result


def main():
    parser = argparse.ArgumentParser(description="Enrich accepted_date and published_date")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--paper-id", type=int, default=None)
    parser.add_argument("--field", choices=["accepted", "published", "both"], default="both",
                        help="Which field(s) to process")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    fields = ["accepted", "published"] if args.field == "both" else [args.field]

    stats = {
        "processed": 0,
        "skip_accepted": 0, "skip_published": 0,
        "accepted": {}, "published": {},
        "error": 0,
    }
    for f in fields:
        stats[f] = {
            "all": 0, "pubmed+pdf": 0, "crossref+pdf": 0, "pubmed+crossref": 0,
            "pubmed": 0, "crossref": 0, "pdf": 0, "missing": 0,
        }

    with get_session(str(ROOT)) as session:
        query = session.query(Paper)
        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            # Only process papers where at least one target field is NULL
            conds = []
            for f in fields:
                conds.append(getattr(Paper, f"{f}_date").is_(None))
            if len(conds) == 1:
                query = query.filter(conds[0])
            else:
                from sqlalchemy import or_
                query = query.filter(or_(*conds))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"Total papers to process: {total}")
        if total == 0:
            return

        for i, paper in enumerate(papers, start=1):
            lines = [f"[{i}/{total}] ID={paper.id} DOI={paper.doi}"]
            try:
                with session.no_autoflush:
                    result = process_paper(paper, force=args.force)
                stats["processed"] += 1

                for f in fields:
                    source, note = result[f]
                    if source == "skip":
                        stats[f"skip_{f}"] += 1
                        lines.append(f"  {f}: SKIP ({note})")
                        continue

                    if source:
                        stats[f][source] = stats[f].get(source, 0) + 1
                    else:
                        stats[f]["missing"] += 1

                    date_str = getattr(paper, f"{f}_date")
                    date_str = date_str.isoformat() if date_str else "N/A"
                    lines.append(f"  {f}: {source or 'MISSING'} | date={date_str}")

                print("\n".join(lines))

                if args.dry_run:
                    session.rollback()
                else:
                    if i % 50 == 0:
                        session.commit()
                        print(f"  -> committed ({i})")


            except Exception as e:
                stats["error"] += 1
                print(f"[{i}/{total}] ID={paper.id} ERROR: {e}")
                if not args.dry_run:
                    session.rollback()

        if not args.dry_run:
            session.commit()

    print("\n===== Summary =====")
    print(f"Processed: {stats['processed']}")
    for f in fields:
        print(f"\n--- {f.upper()} ---")
        d = stats[f]
        print(f"  Skipped:        {stats.get(f'skip_{f}', 0)}")
        print(f"  All agree:      {d['all']}")
        print(f"  PubMed+PDF:     {d['pubmed+pdf']}")
        print(f"  Crossref+PDF:   {d['crossref+pdf']}")
        print(f"  PubMed+Crossref:{d['pubmed+crossref']}")
        print(f"  PubMed only:    {d['pubmed']}")
        print(f"  Crossref only:  {d['crossref']}")
        print(f"  PDF only:       {d['pdf']}")
        print(f"  Missing:        {d['missing']}")
    print(f"\nErrors: {stats['error']}")


if __name__ == "__main__":
    main()
