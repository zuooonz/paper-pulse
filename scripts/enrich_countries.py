"""补全 papers.corresponding_country 字段。

三路提取：CrossRef doi_meta → PDF 全文 → legacy first_authors JSON

Usage:
    python scripts/enrich_countries.py [--dry-run] [--limit N] [--force] [--paper-id ID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.country_extractor import enrich_paper_country


def main():
    parser = argparse.ArgumentParser(description="补全 papers.corresponding_country 字段")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="覆盖已有的 corresponding_country")
    parser.add_argument("--paper-id", type=int, default=None)
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    stats = {"processed": 0, "filled": 0, "skipped": 0, "missing": 0, "error": 0}
    countries: dict[str, int] = {}
    sources: dict[str, int] = {}

    with get_session(str(ROOT)) as session:
        query = session.query(Paper)
        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            query = query.filter(Paper.corresponding_country.is_(None))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"Total papers to process (corresponding_country IS NULL): {total}")
        if total == 0:
            return

        for i, paper in enumerate(papers, start=1):
            try:
                with session.no_autoflush:
                    country, source = enrich_paper_country(paper)

                if country is None and not args.force:
                    stats["missing"] += 1
                    doi_snippet = (paper.doi or "")[:40]
                    print(f"[{i}/{total}] ID={paper.id} DOI={doi_snippet} -> MISSING")
                    continue

                if not args.force and paper.corresponding_country is not None:
                    stats["skipped"] += 1
                    continue

                stats["processed"] += 1

                if country:
                    stats["filled"] += 1
                    countries[country] = countries.get(country, 0) + 1
                    sources[source or "unknown"] = sources.get(source or "unknown", 0) + 1
                    paper.corresponding_country = country
                    paper.corresponding_country_source = source
                    doi_snippet = (paper.doi or "")[:40]
                    print(f"[{i}/{total}] ID={paper.id} country={country} source={source} DOI={doi_snippet}")
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
    print(f"\nCountries: {dict(sorted(countries.items(), key=lambda x: x[1], reverse=True))}")
    print(f"Sources:   {sources}")


if __name__ == "__main__":
    main()
