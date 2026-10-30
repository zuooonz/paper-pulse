#!/usr/bin/env python
"""从 CrossRef 补充缺失的作者信息。

Usage:
    python scripts/fill_missing_authors.py [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.crossref_api import fetch_metadata_by_doi


def extract_authors_from_crossref(raw: dict) -> tuple:
    """从 CrossRef raw 数据中提取第一作者和通讯作者。

    Returns:
        (first_authors_list, corresponding_authors_list)
    """
    authors = raw.get("author", [])
    if not authors:
        return [], []

    first_authors = []
    corresponding_authors = []

    for author in authors:
        given = author.get("given", "")
        family = author.get("family", "")
        name = f"{given} {family}".strip()
        if not name:
            continue

        affiliation = ""
        affs = author.get("affiliation", [])
        if affs and isinstance(affs, list):
            affiliation = affs[0].get("name", "")

        entry = {"name": name, "affiliation": affiliation}

        # 判断作者顺序和角色
        sequence = author.get("sequence", "")
        if sequence == "first":
            entry["role"] = "first"
            first_authors.append(entry)
        else:
            entry["role"] = "co_author"

        # 通讯作者判断（CrossRef 没有直接标记，通常取最后一个或根据 affiliations 判断）
        # 这里简化为：不自动判断通讯作者，只填充 first_authors
        # 如果需要，可以人工补充

    # 如果 first_authors 为空但 authors 不为空，取第一个作为 first
    if not first_authors and authors:
        given = authors[0].get("given", "")
        family = authors[0].get("family", "")
        name = f"{given} {family}".strip()
        affs = authors[0].get("affiliation", [])
        affiliation = affs[0].get("name", "") if affs else ""
        first_authors.append({"name": name, "affiliation": affiliation, "role": "first"})

    return first_authors, corresponding_authors


def fill_missing_authors(workspace: str = ".", config_dir: str = "config", dry_run: bool = False):
    with get_session(workspace, config_dir) as session:
        # 查询缺少作者信息的论文
        papers = list(
            session.query(Paper)
            .filter(
                Paper.doi.isnot(None),
                Paper.first_authors.is_(None),
            )
            .all()
        )

        print(f"Found {len(papers)} papers missing author info")

        updated = 0
        failed = 0
        skipped = 0

        for i, paper in enumerate(papers, 1):
            print(f"\n[{i}/{len(papers)}] internal_id={paper.internal_id}, DOI={paper.doi}")
            print(f"  Title: {paper.title[:80]}...")

            try:
                time.sleep(0.5)
                meta = fetch_metadata_by_doi(paper.doi, structured=True)
                if not meta or meta.get("_error") or meta.get("error"):
                    print(f"  [SKIP] CrossRef query failed: {(meta.get('_error') or meta.get('error')) if meta else 'no response'}")
                    skipped += 1
                    continue

                raw = meta.get("raw") or {}
                first_authors, corresponding_authors = extract_authors_from_crossref(raw)

                if not first_authors:
                    print("  [SKIP] No author info in CrossRef response")
                    skipped += 1
                    continue

                print(f"  First authors: {', '.join(a['name'] for a in first_authors[:3])}")
                if len(first_authors) > 3:
                    print(f"    ... and {len(first_authors) - 3} more")

                if not dry_run:
                    paper.first_authors = first_authors
                    if corresponding_authors:
                        paper.corresponding_authors = corresponding_authors
                    session.flush()

                updated += 1

            except Exception as e:
                print(f"  [ERROR] {e}")
                failed += 1

        if dry_run:
            print("\n[DRY RUN] Rolling back...")
            session.rollback()
        else:
            print("\nChanges committed.")

        print("\n" + "=" * 50)
        print("Summary:")
        print(f"  Total missing: {len(papers)}")
        print(f"  Updated:       {updated}")
        print(f"  Skipped:       {skipped}")
        print(f"  Failed:        {failed}")
        print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="补充缺失的作者信息")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入数据库")
    parser.add_argument("--workspace", default=".", help="工作目录")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    fill_missing_authors(args.workspace, args.config_dir, args.dry_run)


if __name__ == "__main__":
    main()
