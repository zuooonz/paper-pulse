#!/usr/bin/env python
"""修复被 N/A ISSN 污染的数据。

问题：JCR 数据库中部分期刊 ISSN='N/A'，导致 upsert_journal 将不同期刊错误合并。

修复步骤：
1. 删除所有 ISSN='N/A' 的错误期刊记录（保留原始期刊如 ID=4，恢复其 ISSN）
2. 将关联到这些错误记录的论文 journal_id 设为 None
3. 重新用正确的逻辑匹配所有受影响的论文
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from paper_library.db import get_session
from core.cli import add_db_arguments, prepare_database
from paper_library.models import Journal, JournalMetric, Paper
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal


def fix_na_journals(workspace: str = ".", config_dir: str = "config"):
    with get_session(workspace, config_dir) as session:
        # 1. 查找所有 ISSN='N/A' 的期刊
        na_journals = list(session.query(Journal).filter(Journal.issn == "N/A").all())
        print(f"Found {len(na_journals)} journals with ISSN='N/A'")

        # 2. 对于每个 N/A 期刊，处理关联的论文
        affected_paper_ids = []
        for j in na_journals:
            papers = list(session.query(Paper).filter(Paper.journal_id == j.id).all())
            if papers:
                print(f"\n  Journal {j.id}: {j.name}")
                print(f"    Affected papers: {len(papers)}")
                for p in papers:
                    print(f"      internal_id={p.internal_id}: {p.title[:50]}...")
                    affected_paper_ids.append(p.id)

        # 3. 将受影响论文的 journal_id 设为 None（让它们重新匹配）
        if affected_paper_ids:
            print(f"\n  Clearing journal_id for {len(affected_paper_ids)} papers...")
            session.query(Paper).filter(Paper.id.in_(affected_paper_ids)).update(
                {"journal_id": None}, synchronize_session=False
            )
            session.flush()

        # 4. 删除 N/A 期刊及其 metrics
        na_ids = [j.id for j in na_journals]
        if na_ids:
            # 先删除关联的 metrics
            metric_count = session.query(JournalMetric).filter(
                JournalMetric.journal_id.in_(na_ids)
            ).delete(synchronize_session=False)
            print(f"  Deleted {metric_count} journal metrics")

            # 再删除期刊
            journal_count = session.query(Journal).filter(
                Journal.id.in_(na_ids)
            ).delete(synchronize_session=False)
            print(f"  Deleted {journal_count} journals")
            session.flush()

        # 5. 重新匹配受影响的论文
        if affected_paper_ids:
            print(f"\n  Re-matching {len(affected_paper_ids)} papers...")
            matcher = JcrMatcher(workspace=workspace).load()

            for paper_id in affected_paper_ids:
                paper = session.get(Paper, paper_id)
                if not paper or not paper.journal_name:
                    continue

                result = enrich_paper_journal(paper, session, matcher)
                status = result["status"]

                if status == "matched":
                    print(f"    OK  internal_id={paper.internal_id}: {paper.journal_name}")
                elif status == "unmatched":
                    print(f"    --  internal_id={paper.internal_id}: {paper.journal_name} (not in JCR)")

        print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=".")
    fix_na_journals()
