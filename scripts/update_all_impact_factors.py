#!/usr/bin/env python
"""批量更新数据库中所有论文的影响因子和分区信息。

从 jcr.db (ShowJCR) 读取数据，为所有论文补充：
  - JournalMetric (影响因子 + JCR 分区)
  - Paper.cas_quartile (中科院分区)
  - Paper.emerging_quartile (新锐分区)

Usage:
    python scripts/update_all_impact_factors.py [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper, JournalMetric
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal


def update_all(
    workspace: str = ".",
    config_dir: str = "config",
    dry_run: bool = False,
    reference_config: str | None = None,
):
    matcher = JcrMatcher(config_path=reference_config, workspace=workspace).load()
    print(f"JCR matcher loaded: {matcher.stats}")

    with get_session(workspace, config_dir) as session:
        papers = list(session.query(Paper).all())
        print(f"\nTotal papers in DB: {len(papers)}")

        stats = {
            "matched": 0,
            "already_complete": 0,
            "updated_to_latest": 0,
            "unmatched": 0,
            "no_journal_name": 0,
            "metrics_added": 0,
            "cas_added": 0,
            "emerging_added": 0,
        }

        for paper in papers:
            # 跳过没有期刊名的
            if not paper.journal_name:
                stats["no_journal_name"] += 1
                continue

            # 检查是否已有配置所指最新年份和完整分区数据。
            has_all = False
            if paper.journal_id:
                metric = session.scalar(
                    select(JournalMetric).where(
                        JournalMetric.journal_id == paper.journal_id,
                        JournalMetric.year == matcher.jcr_year,
                        JournalMetric.impact_factor.isnot(None),
                        JournalMetric.jcr_quartile.isnot(None),
                        JournalMetric.cas_quartile.isnot(None),
                        JournalMetric.emerging_quartile.isnot(None),
                    )
                )
                has_all = metric is not None

            if has_all:
                stats["already_complete"] += 1
                continue

            # 尝试匹配补全
            result = enrich_paper_journal(paper, session, matcher)

            if result["status"] == "matched":
                stats["matched"] += 1
                details = result.get("details", {})
                if "impact_factor" in details:
                    stats["metrics_added"] += 1
                    if details.get("jcr_year") == matcher.jcr_year:
                        stats["updated_to_latest"] += 1
                if "cas_quartile" in details:
                    stats["cas_added"] += 1
                if "emerging_quartile" in details:
                    stats["emerging_added"] += 1
            elif result["status"] == "unmatched":
                stats["unmatched"] += 1
                print(f"  [未匹配] Paper {paper.id}: {paper.journal_name}")
            else:
                stats["no_journal_name"] += 1

        if dry_run:
            print("\n[DRY RUN] Rolling back changes...")
            session.rollback()
        else:
            print("\nChanges committed.")

        print("\n" + "=" * 50)
        print("Summary:")
        print(f"  Total papers:           {len(papers)}")
        print(f"  Already complete:       {stats['already_complete']}")
        print(f"  Matched & updated:      {stats['matched']}")
        print(f"    → Updated to IF{matcher.jcr_year}:  {stats['updated_to_latest']}")
        print(f"  Unmatched:              {stats['unmatched']}")
        print(f"  No journal name:        {stats['no_journal_name']}")
        print(f"  Impact factors added:   {stats['metrics_added']}")
        print(f"  CAS quartiles added:    {stats['cas_added']}")
        print(f"  Emerging added:         {stats['emerging_added']}")
        print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="批量更新论文影响因子")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入数据库")
    parser.add_argument("--workspace", default=".", help="工作目录")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    parser.add_argument("--reference-config", default=None, help="参考数据配置文件")
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    update_all(args.workspace, args.config_dir, args.dry_run, args.reference_config)


if __name__ == "__main__":
    main()
