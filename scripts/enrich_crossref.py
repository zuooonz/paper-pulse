"""CrossRef 元数据批量增强脚本

为数据库中已有 DOI 但尚未获取 CrossRef 元数据的论文补全:
- doi_meta: 完整 CrossRef 原始 JSON 响应
- abstract: 摘要（不覆盖已有值）
- year: 出版年份（不覆盖已有值）
- journal_name: 期刊名称（不覆盖已有值）

命令行参数:
  --dry-run          仅打印计划操作，不提交修改
  --limit N          仅处理前 N 篇论文
  --paper-id ID      仅处理指定 ID 的论文
"""

import argparse
import time

from sqlalchemy import select

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session, load_database_url
from paper_library.models import Paper
from paper_library.author_ingest import normalize_authors_for_paper
from core.crossref_api import fetch_metadata_by_doi
from paper_library.paper_lifecycle import complete_processing, refresh_metadata_complete


def main():
    parser = argparse.ArgumentParser(
        description="批量从 CrossRef 获取论文元数据增强 DB 记录"
    )
    add_db_arguments(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印计划操作，不提交修改",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅处理前 N 篇论文",
    )
    parser.add_argument(
        "--paper-id",
        type=int,
        default=None,
        help="仅处理指定 ID 的论文",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="工作目录路径（默认: .）",
    )
    parser.add_argument(
        "--config-dir",
        default="config",
        help="配置文件目录（默认: config）",
    )
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    # 统计计数器
    stats = {
        "total": 0,
        "no_doi": 0,
        "already_has_meta": 0,
        "updated": 0,
        "failed": 0,
        "unchanged": 0,
    }

    with get_session(args.workspace, args.config_dir) as session:
        # Debug: check what we have in the database before filtering
        if args.paper_id is not None:
            # Count all papers with this id to see if it exists
            all_stmt = select(Paper).where(Paper.id == args.paper_id)
            all_papers = list(session.scalars(all_stmt).all())
            print(f"[debug] DB URL: {load_database_url(args.workspace, args.config_dir)}")
            print(f"[debug] Total papers with id={args.paper_id}: {len(all_papers)}")
            for p in all_papers:
                print(f"[debug]   Paper id={p.id}, doi={repr(p.doi)}, doi_meta={repr(p.doi_meta)}")

        # 构建查询：有 DOI 的论文；doi_meta 在 Python 层过滤
        # （SQLAlchemy JSON 列将 Python None 存为 JSON "null"，IS NULL 不匹配）
        stmt = select(Paper).where(Paper.doi.isnot(None))
        if args.paper_id is not None:
            stmt = stmt.where(Paper.id == args.paper_id)
        if args.limit is not None:
            stmt = stmt.limit(args.limit)

        all_papers = list(session.scalars(stmt).all())
        papers = [p for p in all_papers if p.doi_meta is None]
        stats["total"] = len(papers)

        if args.dry_run:
            print(f"[dry-run] 计划处理 {len(papers)} 篇论文:")
            for p in papers:
                print(f"  - ID={p.id}, DOI={p.doi}, title={p.title[:60]}...")
            print(f"[dry-run] 统计: 待处理 {len(papers)}，完成。")
            return

        for paper in papers:
            doi = paper.doi.strip() if paper.doi else None
            if not doi:
                stats["no_doi"] += 1
                continue

            if paper.doi_meta is not None:
                stats["already_has_meta"] += 1
                continue

            print(f"Processing ID={paper.id}, DOI={doi}...")

            try:
                # 调用 CrossRef API，structured=True 获取结构化结果含 raw payload
                result = fetch_metadata_by_doi(doi, structured=True)

                if not result or result.get("_error") or result.get("error"):
                    print(f"  WARN: API 返回空，跳过 ID={paper.id}")
                    stats["failed"] += 1
                    continue

                # 更新字段 - 仅当新值非空时更新，不覆盖已有值
                updated = False

                # doi_meta 总是更新（因为查询条件就是它为 None）
                paper.doi_meta = result.get("raw", {})
                updated = True
                if paper.doi_meta:
                    try:
                        with session.begin_nested():
                            normalize_authors_for_paper(paper.doi_meta, paper.id, session)
                    except Exception as e:
                        print(f"  WARN: 作者标准化失败 ID={paper.id}: {e}")

                # abstract 不覆盖已有值
                if result.get("abstract") and not paper.abstract:
                    paper.abstract = result["abstract"]
                    updated = True

                # year 不覆盖已有值
                if result.get("year") is not None and paper.year is None:
                    paper.year = result["year"]
                    updated = True

                # journal_name 不覆盖已有值
                journal_title = result.get("journal", {}).get("title")
                if journal_title and not paper.journal_name:
                    paper.journal_name = journal_title
                    updated = True

                if updated:
                    session.flush()
                    session.expire(paper, ["authors"])
                    refresh_metadata_complete(paper)
                    complete_processing(paper)
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1

            except Exception as e:
                print(f"  ERROR: 处理 ID={paper.id} 失败: {e}")
                stats["failed"] += 1
                continue

            # 礼貌限速：每次请求后 sleep 0.5 秒
            time.sleep(0.5)

        # 提交所有修改
        if stats["updated"] > 0:
            session.commit()
        else:
            session.rollback()

    # 打印最终统计
    print("\n" + "=" * 60)
    print("CrossRef 元数据增强完成:")
    print(f"  待处理: {stats['total']} 篇")
    print(f"  已更新: {stats['updated']} 篇")
    print(f"  无变化: {stats['unchanged']} 篇")
    print(f"  无DOI跳过: {stats['no_doi']} 篇")
    print(f"  请求失败: {stats['failed']} 篇")
    print(f"  已有meta跳过: {stats['already_has_meta']} 篇")
    print("=" * 60)


if __name__ == "__main__":
    main()
