"""补全 publication_month 字段。

五路提取，优先级递减：
  1. published_date（已有，精度 >= month）
  2. Crossref doi_meta（published-online / published-print / issued.date-parts）
  3. PubMed API（via PMID or DOI → PMID）
  4. PDF 全文（extracted_text 正则）
  5. accepted_date（已有，降级近似，source="accepted_proxy"）

Usage:
    python scripts/enrich_months.py [--dry-run] [--limit N] [--force] [--paper-id ID]
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.date_extractor import (
    extract_published_from_crossref_meta,
    extract_published_from_pdf,
    fetch_dates_from_pubmed,
    fetch_dates_from_pubmed_by_doi,
    _fetch_fresh_crossref,
)


def month_from_date(d: Optional[datetime.date]) -> Optional[int]:
    if d is None:
        return None
    return d.month


def extract_month_for_paper(paper: Paper, force: bool = False) -> tuple[Optional[int], Optional[str]]:
    """Extract publication_month for a single paper. Returns (month, source)."""
    if not force and paper.publication_month is not None:
        return None, "skip"

    # Source 1: published_date (already in DB)
    if paper.published_date is not None:
        source = paper.published_date_source or "published_date"
        return paper.published_date.month, source

    # Source 2: Crossref doi_meta
    crossref_meta = paper.doi_meta if (paper.doi_meta and isinstance(paper.doi_meta, dict)) else None
    if not crossref_meta and paper.doi:
        crossref_meta = _fetch_fresh_crossref(paper.doi)

    if crossref_meta:
        d, precision = extract_published_from_crossref_meta(crossref_meta)
        if d and precision in ("month", "day"):
            return d.month, f"crossref({precision})"

    # Source 3: PubMed
    pm_dates = None
    if paper.pmid:
        pm_dates = fetch_dates_from_pubmed(str(paper.pmid))
    elif paper.doi:
        pm_dates = fetch_dates_from_pubmed_by_doi(paper.doi)

    if pm_dates:
        d, precision = pm_dates.get("published", (None, ""))
        if d and precision in ("month", "day"):
            return d.month, f"pubmed({precision})"

    # Source 4: PDF full text
    if paper.extracted_text:
        d, precision = extract_published_from_pdf(paper.extracted_text)
        if d and precision in ("month", "day"):
            return d.month, f"pdf({precision})"

    # Source 5: accepted_date (降级近似)
    if paper.accepted_date is not None:
        return paper.accepted_date.month, "accepted_proxy"

    # Source 6: Crossref fallback (year-level precision — still better than nothing)
    if crossref_meta:
        d, precision = extract_published_from_crossref_meta(crossref_meta)
        if d:
            return d.month, f"crossref({precision})"

    return None, None


def main():
    parser = argparse.ArgumentParser(description="补全 publication_month 字段")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="覆盖已有的 publication_month")
    parser.add_argument("--paper-id", type=int, default=None)
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    stats = {"processed": 0, "filled": 0, "skipped": 0, "missing": 0, "error": 0}
    sources: dict[str, int] = {}

    with get_session(str(ROOT)) as session:
        query = session.query(Paper)
        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            query = query.filter(Paper.publication_month.is_(None))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"Total papers to process: {total}")
        if total == 0:
            return

        for i, paper in enumerate(papers, start=1):
            try:
                with session.no_autoflush:
                    month, source = extract_month_for_paper(paper, force=args.force)

                if source == "skip":
                    stats["skipped"] += 1
                    continue

                stats["processed"] += 1

                if month is not None:
                    stats["filled"] += 1
                    sources[source] = sources.get(source, 0) + 1
                    paper.publication_month = month
                    paper.publication_month_source = source
                    print(f"[{i}/{total}] ID={paper.id} month={month} source={source}")
                else:
                    stats["missing"] += 1
                    print(f"[{i}/{total}] ID={paper.id} MISSING")

                if args.dry_run:
                    session.rollback()
                elif i % 50 == 0:
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
    print(f"Filled:    {stats['filled']}")
    print(f"Skipped:   {stats['skipped']}")
    print(f"Missing:   {stats['missing']}")
    print(f"Errors:    {stats['error']}")
    print(f"\nSources: {sources}")


if __name__ == "__main__":
    main()
