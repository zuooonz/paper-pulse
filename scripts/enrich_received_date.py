"""Enrich received_date for papers via triple-source extraction:
- PubMed API (DOI -> PMID -> History/received)
- Crossref metadata (assertion from existing doi_meta or fresh API call)
- PDF full-text regex

Usage:
    python scripts/enrich_received_date.py [--dry-run] [--limit N] [--force] [--only-missing]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Ensure src/ is on path
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.received_date_extractor import (
    extract_from_pdf_text,
    fetch_from_pubmed_by_doi,
    fetch_from_pubmed_by_pmid,
    extract_from_crossref_meta,
    fetch_from_crossref_by_doi,
    cross_check,
)


def process_paper(paper: Paper, force: bool = False) -> tuple[Optional[str], Optional[str]]:
    """Process a single paper. Returns (source_tag, note)."""
    if not force and paper.received_date is not None:
        return "skip", "already_has_date"

    # --- Source 1: PubMed ---
    date_pubmed = None
    if paper.pmid:
        date_pubmed = fetch_from_pubmed_by_pmid(str(paper.pmid))
    if not date_pubmed and paper.doi:
        date_pubmed = fetch_from_pubmed_by_doi(paper.doi)

    # --- Source 2: Crossref ---
    date_crossref = None
    if paper.doi_meta and isinstance(paper.doi_meta, dict):
        date_crossref = extract_from_crossref_meta(paper.doi_meta)
    # Fallback to fresh Crossref API only if no doi_meta at all
    if not date_crossref and paper.doi and not paper.doi_meta:
        date_crossref = fetch_from_crossref_by_doi(paper.doi)

    # --- Source 3: PDF text ---
    date_pdf = None
    if paper.extracted_text:
        date_pdf = extract_from_pdf_text(paper.extracted_text)

    # --- Cross-check all three ---
    best_date, source = cross_check(date_pubmed, date_crossref, date_pdf)

    if best_date:
        paper.received_date = best_date
        paper.received_date_source = source
        return source, None
    return None, "no_date_found"


def main():
    parser = argparse.ArgumentParser(description="Enrich received_date from PubMed + Crossref + PDF")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of papers")
    parser.add_argument("--force", action="store_true", help="Re-process papers that already have received_date")
    parser.add_argument("--only-missing", action="store_true", help="Only process papers without received_date (default)")
    parser.add_argument("--paper-id", type=int, default=None, help="Process a single paper by ID")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    stats = {
        "processed": 0,
        "skip": 0,
        "all": 0,
        "pubmed+pdf": 0,
        "crossref+pdf": 0,
        "pubmed+crossref": 0,
        "pubmed": 0,
        "crossref": 0,
        "pdf": 0,
        "missing": 0,
        "error": 0,
    }

    with get_session(str(ROOT)) as session:
        query = session.query(Paper)

        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            query = query.filter(Paper.received_date.is_(None))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"Total papers to process: {total}")
        if total == 0:
            return

        for i, paper in enumerate(papers, start=1):
            print(f"[{i}/{total}] ID={paper.id} DOI={paper.doi}")
            try:
                source, note = process_paper(paper, force=args.force)
                stats["processed"] += 1

                if source == "skip":
                    stats["skip"] += 1
                    print(f"  -> SKIP: {note}")
                    continue

                if source:
                    stats[source] = stats.get(source, 0) + 1
                else:
                    stats["missing"] += 1

                date_str = paper.received_date.isoformat() if paper.received_date else "N/A"
                print(f"  -> {source or 'MISSING'} | date={date_str}")

                if args.dry_run:
                    session.rollback()
                else:
                    if i % 50 == 0:
                        session.commit()
                        print(f"  -> committed ({i})")

                if source != "skip":
                    time.sleep(0.11)

            except Exception as e:
                stats["error"] += 1
                print(f"  -> ERROR: {e}")
                if not args.dry_run:
                    session.rollback()

        if not args.dry_run:
            session.commit()

    print("\n===== Summary =====")
    print(f"Processed:      {stats['processed']}")
    print(f"Skipped:        {stats['skip']}")
    print(f"All agree:      {stats['all']}")
    print(f"PubMed+PDF:     {stats['pubmed+pdf']}")
    print(f"Crossref+PDF:   {stats['crossref+pdf']}")
    print(f"PubMed+Crossref:{stats['pubmed+crossref']}")
    print(f"PubMed only:    {stats['pubmed']}")
    print(f"Crossref only:  {stats['crossref']}")
    print(f"PDF only:       {stats['pdf']}")
    print(f"Missing:        {stats['missing']}")
    print(f"Errors:         {stats['error']}")


if __name__ == "__main__":
    main()
