"""Export the paper library as Markdown files suitable for knowledge bases.

Usage examples:
    python scripts/export_markdown.py --limit 10 --output-dir output
    python scripts/export_markdown.py --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from tools.storage import get_output_dir
from tools.markdown_exporter import (
    export_papers_to_directory,
    export_papers_merged,
    load_papers_for_export,
)


def parse_args():
    parser = argparse.ArgumentParser(description="将论文库导出为 Markdown 文档")
    add_db_arguments(parser)
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(get_output_dir(ROOT)),
        help="输出目录 (默认: output/)",
    )
    parser.add_argument(
        "--internal-id-range",
        type=str,
        help="按 internal_id 范围筛选，如 MIN-MAX",
    )
    parser.add_argument(
        "--paper-ids",
        type=str,
        help="指定论文 ID（逗号分隔）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="限制导出数量（测试用）",
    )
    parser.add_argument(
        "--include-full-text",
        action="store_true",
        help="包含全文（默认仅摘要）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印统计，不生成文件",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="合并为单个 Markdown 文件（而非逐篇文件）",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="paper_library.md",
        help="合并模式下的输出文件名 (默认: paper_library.md)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    prepare_database(args, workspace=str(ROOT))

    # 解析参数
    internal_id_range = None
    if args.internal_id_range:
        parts = args.internal_id_range.split("-")
        if len(parts) != 2:
            print("错误: --internal-id-range 格式应为 MIN-MAX，如 MIN-MAX")
            sys.exit(1)
        internal_id_range = (int(parts[0]), int(parts[1]))

    paper_ids = None
    if args.paper_ids:
        paper_ids = [int(x.strip()) for x in args.paper_ids.split(",")]

    output_dir = Path(args.output_dir)

    print("=" * 60)
    print("论文库 Markdown 导出")
    print("=" * 60)

    if internal_id_range:
        print(f"范围: internal_id {internal_id_range[0]}-{internal_id_range[1]}")
    if paper_ids:
        print(f"指定论文: {paper_ids}")
    if args.limit:
        print(f"限制: {args.limit} 篇")
    if args.include_full_text:
        print("包含全文: 是")
    if args.dry_run:
        print("模式: dry-run（不生成文件）")
    if args.merge:
        print(f"模式: 合并为单文件 ({args.output_file})")
    else:
        print(f"输出目录: {output_dir}")
    print()

    with get_session(str(ROOT), "config") as session:
        # 加载数据
        print("加载论文数据...")
        papers = load_papers_for_export(
            session,
            internal_id_range=internal_id_range,
            paper_ids=paper_ids,
            limit=args.limit,
        )
        print(f"加载了 {len(papers)} 篇论文")
        print()

        if args.merge:
            # 合并模式：输出单个文件
            output_file = output_dir / args.output_file
            print(f"导出到: {output_file}")
            stats = export_papers_merged(
                output_file,
                papers,
                include_full_text=args.include_full_text,
                dry_run=args.dry_run,
            )
        else:
            # 分文件模式：每篇一个 .md
            papers_dir = output_dir / "papers"
            print(f"导出到: {papers_dir}")
            stats = export_papers_to_directory(
                session,
                papers_dir,
                papers,
                include_full_text=args.include_full_text,
                dry_run=args.dry_run,
            )

    # 输出统计
    print()
    print("=" * 60)
    print("导出统计")
    print("=" * 60)
    print(f"总数:   {stats.total}")
    print(f"导出:   {stats.exported}")
    print(f"跳过:   {stats.skipped}")
    print(f"错误:   {len(stats.errors)}")
    size_mb = stats.total_bytes / (1024 * 1024)
    print(f"总大小: {size_mb:.2f} MB")

    if stats.errors:
        print("\n错误列表:")
        for err in stats.errors[:10]:
            print(f"  - {err}")
        if len(stats.errors) > 10:
            print(f"  ... 还有 {len(stats.errors) - 10} 个错误")

    # 写入统计 JSON
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        stats_file = output_dir / "export_stats.json"
        stats_data = {
            "total": stats.total,
            "exported": stats.exported,
            "skipped": stats.skipped,
            "errors_count": len(stats.errors),
            "total_bytes": stats.total_bytes,
            "internal_id_range": list(internal_id_range) if internal_id_range else None,
            "limit": args.limit,
            "include_full_text": args.include_full_text,
        }
        stats_file.write_text(
            json.dumps(stats_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n统计文件: {stats_file}")

    print()
    print("完成！" if not args.dry_run else "dry-run 完成，未生成文件")


if __name__ == "__main__":
    main()
