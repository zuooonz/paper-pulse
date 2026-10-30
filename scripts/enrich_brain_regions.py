"""补全 papers.brain_regions 字段：从摘要/全文提取脑区信息。

Usage:
    python scripts/enrich_brain_regions.py [--dry-run] [--limit N] [--force] [--paper-id ID]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import or_, func

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.brain_region_detector import extract_brain_regions_with_paper

def _brain_regions_value(regions: list[str] | None) -> list[str]:
    """Keep JSON columns as native Python values for SQLAlchemy to serialize."""
    return list(regions or [])


def main():
    parser = argparse.ArgumentParser(description="补全 papers.brain_regions 字段（摘要/全文关键词提取）")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="覆盖已有的 brain_regions")
    parser.add_argument("--paper-id", type=int, default=None)
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))
    stats = {"processed": 0, "filled": 0, "empty": 0, "skipped": 0, "no_text": 0, "error": 0}
    region_counts: dict[str, int] = {}

    with get_session(str(ROOT)) as session:
        # 过滤：有文本（摘要或全文）
        query = session.query(Paper).filter(
            or_(
                Paper.abstract.isnot(None) & (func.length(Paper.abstract) > 50),
                Paper.extracted_text.isnot(None) & (func.length(Paper.extracted_text) > 100),
            )
        )

        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            query = query.filter(Paper.brain_regions.is_(None))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"Total papers to process (brain_regions IS NULL + has text): {total}")
        if total == 0:
            print("No papers match. Try --force to re-process.")
            return

        for i, paper in enumerate(papers, start=1):
            try:
                with session.no_autoflush:
                    regions = extract_brain_regions_with_paper(paper)

                if not regions:
                    stats["empty"] += 1
                    paper.brain_regions = _brain_regions_value([])  # 标记已处理
                    if i <= 20 or i % 200 == 0:
                        print(f"[{i}/{total}] ID={paper.id} -> (empty)")
                else:
                    stats["filled"] += 1
                    paper.brain_regions = _brain_regions_value(regions)
                    for r in regions:
                        region_counts[r] = region_counts.get(r, 0) + 1
                    if i <= 20 or i % 100 == 0:
                        print(f"[{i}/{total}] ID={paper.id} -> {regions}")

                stats["processed"] += 1

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
    print(f"Empty:     {stats['empty']}")
    print(f"Errors:    {stats['error']}")

    print("\nBrain region distribution (top 20):")
    for region, count in sorted(region_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {region}: {count}")


if __name__ == "__main__":
    main()
