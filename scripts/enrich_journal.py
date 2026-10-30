#!/usr/bin/env python3
"""使用统一参考数据配置匹配论文期刊的影响因子和分区。"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.journal_enrichment import JcrEntry, JcrMatcher, normalize_journal
from paper_library.models import Journal, JournalMetric, Paper
from paper_library.crud import upsert_journal


class JournalData(dict[str, dict]):
    """Compatibility mapping that keeps the configured matcher for fuzzy lookup."""

    def __init__(self, matcher: JcrMatcher):
        super().__init__((key, asdict(entry)) for key, entry in matcher._entries.items())
        self.matcher = matcher


def load_jcr_data(
    jcr_db_path: str | Path | None = None,
    *,
    config_path: str | Path | None = None,
    workspace: str | Path = ".",
) -> JournalData:
    matcher = JcrMatcher(
        jcr_db_path=jcr_db_path,
        config_path=config_path,
        workspace=workspace,
    ).load()
    return JournalData(matcher)


def upsert_journal_metric(
    session: Session,
    journal: Journal,
    year: int,
    impact_factor: Optional[float],
    quartile: Optional[str],
) -> JournalMetric:
    stmt = select(JournalMetric).where(
        JournalMetric.journal_id == journal.id,
        JournalMetric.year == year,
    )
    existing = session.scalar(stmt)
    if existing:
        if impact_factor is not None:
            existing.impact_factor = impact_factor
        if quartile is not None:
            existing.jcr_quartile = quartile
        session.add(existing)
        session.flush()
        return existing
    metric = JournalMetric(
        journal_id=journal.id,
        year=year,
        impact_factor=impact_factor,
        jcr_quartile=quartile,
    )
    session.add(metric)
    session.flush()
    return metric


def main():
    parser = argparse.ArgumentParser(description="使用参考数据配置关联期刊指标")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写入数据库")
    parser.add_argument("--limit", type=int, default=None, help="限制处理数量")
    parser.add_argument("--workspace", default=".", help="工作目录")
    parser.add_argument("--config-dir", default="config", help="运行配置目录")
    parser.add_argument("--reference-config", default=None, help="参考数据 YAML 路径；默认使用 REFERENCE_CONFIG 或 config/reference.yaml")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    data = load_jcr_data(config_path=args.reference_config, workspace=args.workspace)
    matcher = getattr(data, "matcher", None)
    if matcher:
        print(f"Loaded {matcher.stats['entries']} journals from {matcher.jcr_db_path}")
    else:
        print(f"Loaded {len(data)} journals")

    stats = {"skipped": 0, "matched": 0, "unmatched": 0}
    with get_session(args.workspace, args.config_dir) as session:
        stmt = select(Paper).where(Paper.journal_name.isnot(None), Paper.journal_id.is_(None))
        if args.limit:
            stmt = stmt.limit(args.limit)
        papers = list(session.scalars(stmt).all())
        print(f"\nFound {len(papers)} papers waiting for journal matching")

        for paper in papers:
            if matcher:
                entry = matcher.match(journal_name=paper.journal_name)
            else:
                item = data.get(normalize_journal(paper.journal_name))
                entry = JcrEntry(
                    name=item["name"],
                    issn=item.get("issn"),
                    eissn=item.get("eissn"),
                    impact_factor=item.get("impact_factor"),
                    jcr_quartile=item.get("quartile"),
                    jcr_year=item.get("year", 2025),
                ) if item else None
            if entry is None:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            if args.dry_run:
                print(
                    f"  [DRY RUN] Would match paper {paper.id} ({paper.title[:60]}...) -> "
                    f"{entry.name}, IF={entry.impact_factor}, {entry.jcr_quartile}"
                )
                continue
            journal = upsert_journal(session, {
                "name": entry.name,
                "issn": entry.issn,
                "eissn": entry.eissn,
            })
            upsert_journal_metric(
                session,
                journal,
                year=entry.jcr_year,
                impact_factor=entry.impact_factor,
                quartile=entry.jcr_quartile,
            )
            paper.journal_id = journal.id
            print(
                f"  Matched paper {paper.id}: {paper.title[:60]}... -> {journal.name} "
                f"(IF={entry.impact_factor}, {entry.jcr_quartile})"
            )

        print("\n" + "=" * 60)
        print("Summary:")
        print(f"  Papers skipped (already had journal_id): {stats['skipped']}")
        print(f"  Papers matched: {stats['matched']}")
        print(f"  Papers unmatched: {stats['unmatched']}")
        print("=" * 60)
        if args.dry_run:
            print("\n[DRY RUN] No changes committed to database")
        else:
            print("\nChanges committed to database")


if __name__ == "__main__":
    main()
