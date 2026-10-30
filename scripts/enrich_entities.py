"""补全论文关联实体字段：基于 PDF 全文识别配置中的实体词条。

Usage:
    python scripts/enrich_entities.py [--dry-run] [--limit N] [--force] [--paper-id ID]
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
from core.entity_detector import detect_entity_with_paper, MIN_SCORE_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description="从论文全文补全关联实体（关键词检索）")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="覆盖已有的实体标注")
    parser.add_argument("--paper-id", type=int, default=None)
    parser.add_argument("--min-score", type=int, default=MIN_SCORE_THRESHOLD,
                        help=f"最低评分阈值 (default: {MIN_SCORE_THRESHOLD})")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    stats = {"processed": 0, "filled": 0, "skipped": 0, "missing": 0, "error": 0}
    entities: dict[str, int] = {}
    confidences: dict[str, int] = {}

    with get_session(str(ROOT)) as session:
        query = session.query(Paper).filter(
            Paper.pdf_text_extracted == True,
            Paper.extracted_text.isnot(None),
        )
        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        elif not args.force:
            query = query.filter(Paper.entity_label.is_(None))

        if args.limit:
            query = query.limit(args.limit)

        papers = query.all()
        total = len(papers)
        print(f"待处理论文（未标注实体且有全文）：{total}")
        if total == 0:
            print("没有符合条件的论文；可使用 --force 重新处理已有实体标注。")
            return

        for i, paper in enumerate(papers, start=1):
            try:
                with session.no_autoflush:
                    entity_label, confidence, scores = detect_entity_with_paper(paper)

                if entity_label is None and not args.force:
                    stats["missing"] += 1
                    print(f"[{i}/{total}] ID={paper.id} DOI={paper.doi} -> MISSING (scores: {scores})")
                    continue

                if not args.force and paper.entity_label is not None:
                    stats["skipped"] += 1
                    continue

                stats["processed"] += 1

                if entity_label:
                    stats["filled"] += 1
                    entities[entity_label] = entities.get(entity_label, 0) + 1
                    confidences[confidence or "unknown"] = confidences.get(confidence or "unknown", 0) + 1
                    paper.entity_label = entity_label
                    paper.entity_source = "fulltext_keyword"
                    paper.entity_confidence = confidence
                    print(f"[{i}/{total}] ID={paper.id} entity={entity_label} confidence={confidence} scores={scores}")
                else:
                    stats["missing"] += 1
                    print(f"[{i}/{total}] ID={paper.id} MISSING (scores={scores})")

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
    print(f"\nEntities: {entities}")
    print(f"Confidences: {confidences}")


if __name__ == "__main__":
    main()
