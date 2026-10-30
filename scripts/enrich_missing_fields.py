#!/usr/bin/env python3
"""Phase 2: 从 CrossRef doi_meta 补全缺失字段（issue / pages / abstract / journal_id）

功能：
1. 对已有 doi_meta 的论文，提取 issue、page、abstract 补全到 papers 表
2. abstract 为 JATS XML 格式，自动去除标签提取纯文本
3. 对无 journal_id 的论文，通过 journal_name 模糊匹配 journals 表

原则：
- 不覆盖已有非空值（尊重手工录入或之前已补全的数据）
- 全部从 CrossRef doi_meta 自动提取，不手动硬编码
"""

import re
import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper, Journal


def strip_jats(xml_text: str) -> str:
    """去除 JATS XML 标签，提取纯文本。"""
    if not xml_text:
        return ""
    # 先替换常见 HTML 实体
    text = xml_text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    # 去除所有 XML/HTML 标签
    text = re.sub(r'<[^>]+>', ' ', text)
    # 规范化空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def main():
    parser = argparse.ArgumentParser(description="补全缺失论文字段")
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=str(PROJECT_ROOT))

    print("=" * 60)
    print("Phase 2: 缺失字段批量补全")
    print("=" * 60)

    with get_session(workspace=str(PROJECT_ROOT), config_dir="config") as session:
        # 1. 从 doi_meta 补全 issue / pages / abstract
        papers = session.query(Paper).where(Paper.doi_meta.isnot(None)).all()

        issue_filled = 0
        pages_filled = 0
        abstract_filled = 0
        skipped_abstract_xml = 0

        for p in papers:
            meta = p.doi_meta
            if not meta or not isinstance(meta, dict):
                continue
            msg = meta.get("message") if "message" in meta else meta
            if not msg or not isinstance(msg, dict):
                continue

            # issue
            if not p.issue:
                crossref_issue = msg.get("issue")
                if crossref_issue:
                    p.issue = str(crossref_issue)
                    issue_filled += 1

            # pages (CrossRef uses "page")
            if not p.pages:
                crossref_page = msg.get("page")
                if crossref_page:
                    p.pages = str(crossref_page)
                    pages_filled += 1

            # abstract (JATS XML)
            if not p.abstract:
                crossref_abstract = msg.get("abstract")
                if crossref_abstract:
                    if isinstance(crossref_abstract, str):
                        plain = strip_jats(crossref_abstract)
                        if plain and len(plain) > 20:  # 过滤过短的无效摘要
                            p.abstract = plain
                            abstract_filled += 1
                        else:
                            skipped_abstract_xml += 1
                    elif isinstance(crossref_abstract, dict):
                        # 有些 abstract 是结构化对象，暂跳过
                        skipped_abstract_xml += 1

        session.commit()

        print("\nCrossRef 字段补全：")
        print(f"  issue 补全: {issue_filled}")
        print(f"  pages 补全: {pages_filled}")
        print(f"  abstract 补全: {abstract_filled}")
        print(f"  abstract 跳过（格式异常）: {skipped_abstract_xml}")

        # 2. journal_id 匹配
        papers_no_journal = session.query(Paper).where(
            Paper.journal_id.is_(None),
            Paper.journal_name.isnot(None)
        ).all()

        # 预加载所有期刊
        journals = session.query(Journal).all()
        journal_map = {}
        for j in journals:
            key = j.name.strip().lower() if j.name else ""
            if key:
                journal_map[key] = j.id
            if j.name_abbr:
                abbr_key = j.name_abbr.strip().lower()
                if abbr_key and abbr_key not in journal_map:
                    journal_map[abbr_key] = j.id

        journal_matched = 0
        journal_unmatched = []

        for p in papers_no_journal:
            jname = (p.journal_name or "").strip().lower()
            matched_id = journal_map.get(jname)
            if matched_id:
                p.journal_id = matched_id
                journal_matched += 1
            else:
                journal_unmatched.append(p.journal_name)

        session.commit()

        print("\nJournal 匹配：")
        print(f"  待匹配论文: {len(papers_no_journal)}")
        print(f"  成功匹配: {journal_matched}")
        print(f"  未匹配: {len(journal_unmatched)}")
        if journal_unmatched:
            unique_unmatched = sorted(set(journal_unmatched))
            print("  未匹配期刊名（去重）:")
            for name in unique_unmatched[:10]:
                print(f"    - {name}")
            if len(unique_unmatched) > 10:
                print(f"    ... and {len(unique_unmatched) - 10} more")

        # 3. 最终统计
        total = session.query(Paper).count()
        missing_issue = session.query(Paper).where(
            (Paper.issue.is_(None)) | (Paper.issue == "")
        ).count()
        missing_pages = session.query(Paper).where(
            (Paper.pages.is_(None)) | (Paper.pages == "")
        ).count()
        missing_abstract = session.query(Paper).where(
            (Paper.abstract.is_(None)) | (Paper.abstract == "")
        ).count()
        missing_journal_id = session.query(Paper).where(Paper.journal_id.is_(None)).count()

        print("\n补全后统计：")
        print(f"  论文总数: {total}")
        print(f"  仍缺 issue: {missing_issue} ({missing_issue/total*100:.1f}%)")
        print(f"  仍缺 pages: {missing_pages} ({missing_pages/total*100:.1f}%)")
        print(f"  仍缺 abstract: {missing_abstract} ({missing_abstract/total*100:.1f}%)")
        print(f"  仍缺 journal_id: {missing_journal_id}")

    print("\n" + "=" * 60)
    print("Phase 2 完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
