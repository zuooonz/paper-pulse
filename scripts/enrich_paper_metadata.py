#!/usr/bin/env python3
"""
批量补全论文元数据：期刊关联、影响因子、分区、cites_target

用法：
    python scripts/enrich_paper_metadata.py [--dry-run] [--limit N]
"""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper, Journal
from core.entity_detector import detect_entity, get_target_label


def link_papers_to_journals(session, dry_run: bool = False):
    """通过 doi_meta 的 container-title 和 ISSN 建立 Paper → Journal 关联。"""
    stats = {"linked": 0, "created": 0, "skipped": 0, "no_meta": 0}

    papers = session.query(Paper).all()
    print(f"\n=== 论文-期刊关联 ({len(papers)} 篇) ===")

    for p in papers:
        # 1. 尝试从 doi_meta 获取正确的期刊信息
        doi_meta = p.doi_meta or {}
        container_titles = doi_meta.get("container-title") or []
        issns = doi_meta.get("ISSN") or []

        correct_name = container_titles[0] if container_titles else None
        issn = issns[0] if issns else None

        if not correct_name:
            stats["no_meta"] += 1
            continue

        # 2. 查找或创建 Journal（大小写不敏感匹配）
        journal = session.query(Journal).filter(Journal.name.ilike(correct_name)).first()
        if not journal and issn:
            journal = session.query(Journal).filter(Journal.issn == issn).first()

        if not journal:
            if dry_run:
                print(f"  [DRY] Would create journal: {correct_name} (ISSN: {issn})")
            else:
                journal = Journal(
                    name=correct_name,
                    issn=issn or "N/A",
                )
                session.add(journal)
                session.flush()
                print(f"  Created journal: {correct_name} (id={journal.id}, ISSN={issn})")
            stats["created"] += 1
        else:
            stats["linked"] += 1

        if not dry_run and journal:
            # 更新论文的 journal_id 和 journal_name
            if p.journal_id != journal.id:
                p.journal_id = journal.id
            if p.journal_name != correct_name:
                p.journal_name = correct_name

    if not dry_run:
        session.commit()

    print(f"\n关联完成: 已关联 {stats['linked']}, 新建期刊 {stats['created']}, 无元数据 {stats['no_meta']}")
    return stats


def fix_missing_journal_names(session, dry_run: bool = False):
    """为缺失 journal_name 的论文补全期刊名。"""
    papers = session.query(Paper).filter(Paper.journal_name == None).all()
    if not papers:
        print("\n=== 无缺失 journal_name 的论文 ===")
        return

    print(f"\n=== 补全缺失的 journal_name ({len(papers)} 篇) ===")
    for p in papers:
        doi_meta = p.doi_meta or {}
        container_titles = doi_meta.get("container-title") or []
        correct_name = container_titles[0] if container_titles else None

        if correct_name:
            if not dry_run:
                p.journal_name = correct_name
            print(f"  ID {p.internal_id}: {correct_name}")
        else:
            # 预印本或缺失 DOI 的论文
            if p.doi and "arxiv" in p.doi.lower():
                name = "arXiv Preprint"
            elif p.doi and "ssrn" in p.doi.lower():
                name = "SSRN Preprint"
            elif p.doi and "researchsquare" in p.doi.lower() or "rs.3.rs" in (p.doi or ""):
                name = "Research Square Preprint"
            elif p.doi and ("biorxiv" in p.doi.lower() or "medrxiv" in p.doi.lower()):
                name = "bioRxiv Preprint"
            else:
                name = "Unknown"
            if not dry_run:
                p.journal_name = name
            print(f"  ID {p.internal_id}: {name} (preprint/no DOI)")

    if not dry_run:
        session.commit()
    print(f"补全完成: {len(papers)} 篇")


def check_cites_target(session, dry_run: bool = False):
    """从 extracted_text 中重新检查 cites_target。"""
    target_label = get_target_label()
    if not target_label:
        print("\n=== 目标实体未配置，跳过关联检查 ===")
        return {"disabled": True}
    papers = session.query(Paper).filter(Paper.extracted_text != None).all()
    print(f"\n=== 检查 cites_target ({len(papers)} 篇有提取文本) ===")

    stats = {"changed_to_true": 0, "changed_to_false": 0, "unchanged": 0, "no_text": 0}

    for p in papers:
        text = p.extracted_text or ""
        detected_label, _, _ = detect_entity(text)
        has_cite = detected_label == target_label

        if has_cite != p.cites_target:
            if not dry_run:
                p.cites_target = has_cite
            action = "→ True" if has_cite else "→ False"
            print(f"  ID {p.internal_id}: {p.cites_target} {action}")
            if has_cite:
                stats["changed_to_true"] += 1
            else:
                stats["changed_to_false"] += 1
        else:
            stats["unchanged"] += 1

    # 没有提取文本的论文
    no_text = session.query(Paper).filter(Paper.extracted_text == None).count()
    stats["no_text"] = no_text

    if not dry_run:
        session.commit()

    print("\n检查完成:")
    print(f"  改为 True: {stats['changed_to_true']}")
    print(f"  改为 False: {stats['changed_to_false']}")
    print(f"  未变化: {stats['unchanged']}")
    print(f"  无提取文本: {stats['no_text']}")
    return stats


def fetch_impact_factors(session, dry_run: bool = False):
    """
    尝试从外部获取缺失期刊的影响因子。
    当前策略：打印缺失列表，提示手动补充。
    """

    # 找到所有没有 JournalMetric 的 Journal
    journals_without_metrics = (
        session.query(Journal)
        .filter(~Journal.metrics.any())
        .all()
    )

    if not journals_without_metrics:
        print("\n=== 所有期刊都有影响因子数据 ===")
        return

    print(f"\n=== 缺失影响因子的期刊 ({len(journals_without_metrics)} 个) ===")
    for j in journals_without_metrics:
        print(f"  - {j.name} (ISSN: {j.issn})")

    print("\n提示: 请手动从 JCR/Scimago 获取这些数据，或使用外部 API 批量导入。")


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="批量补全论文元数据")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写入数据库")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    with get_session(args.workspace, args.config_dir) as session:
        print(f"[{'DRY-RUN' if args.dry_run else 'LIVE'} MODE]")

        # 1. 关联论文与期刊
        link_papers_to_journals(session, dry_run=args.dry_run)

        # 2. 补全缺失的 journal_name
        fix_missing_journal_names(session, dry_run=args.dry_run)

        # 3. 重新检查 cites_target
        check_cites_target(session, dry_run=args.dry_run)

        # 4. 列出缺失影响因子的期刊
        fetch_impact_factors(session, dry_run=args.dry_run)

        print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
