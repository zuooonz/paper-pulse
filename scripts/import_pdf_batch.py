#!/usr/bin/env python3
"""
PDF 批量导入脚本（本地开发版）

用法：
  python scripts/import_pdf_batch.py --scan-dir ./new_pdfs --dry-run
  python scripts/import_pdf_batch.py --scan-dir ./new_pdfs --import
  python scripts/import_pdf_batch.py --scan-dir ./new_pdfs --import --yes
"""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import func
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.pdf_import import PdfImportResult, process_pdf_import
from tools.storage import get_pdf_dir

PDF_DIR = get_pdf_dir(ROOT)


_IMPORTED_STATUSES = ("ok", "created", "linked", "replaced", "updated", "existing_with_pdf")


def _category(r) -> str:
    """Report bucket: Crossref match, PDF-metadata fallback, or the failure status."""
    if getattr(r, "fallback", False) and r.status not in ("mismatch", "no_doi", "error"):
        return "fallback"
    return "ok" if r.status in _IMPORTED_STATUSES else r.status


def print_report(results: list[PdfImportResult]):
    print("\n" + "=" * 70)
    print("PDF 批量导入报告")
    print("=" * 70)

    categories = {"ok": [], "fallback": [], "mismatch": [], "no_doi": [], "error": []}
    for r in results:
        categories.get(_category(r), []).append(r)

    print(f"\n总计: {len(results)} 个 PDF")
    print(f"  [OK] Crossref 匹配:       {len(categories['ok'])}")
    print(f"  [INFO] PDF 元数据回退:    {len(categories['fallback'])}")
    print(f"  [WARN] 标题不匹配:        {len(categories['mismatch'])}")
    print(f"  [ERR] 无 DOI:             {len(categories['no_doi'])}")
    print(f"  [ERR] 错误:               {len(categories['error'])}")

    if categories["mismatch"]:
        print("\n--- 标题不匹配的 PDF ---")
        for r in categories["mismatch"]:
            print(f"  文件: {r.filename}")
            print(f"    DOI: {r.doi}")
            print(f"    CrossRef: {(r.title or 'N/A')[:100]}...")
            print(f"    PDF 候选: {(r.pdf_title or 'N/A')[:100]}...")
            print(f"    相似度: {r.similarity:.2f}")

    if categories["error"]:
        print("\n--- 错误的 PDF ---")
        for r in categories["error"]:
            print(f"  文件: {r.filename}  原因: {r.error_msg}")

    if categories["no_doi"]:
        print("\n--- 无 DOI 的 PDF ---")
        for r in categories["no_doi"]:
            print(f"  文件: {r.filename}")

    print("=" * 70)


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="PDF 批量导入脚本（本地）")
    add_db_arguments(parser)
    parser.add_argument("--scan-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--replace-existing", action="store_true", help="覆盖已有 PDF")
    parser.add_argument("--skip-title-check", action="store_true")
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    scan_dir = Path(args.scan_dir)
    if not scan_dir.exists() or not scan_dir.is_dir():
        print(f"[错误] 目录不存在: {scan_dir}")
        sys.exit(1)

    pdf_paths = sorted(scan_dir.glob("*.pdf"))
    if not pdf_paths:
        print(f"[提示] 目录中没有 PDF 文件: {scan_dir}")
        sys.exit(0)

    print(f"发现 {len(pdf_paths)} 个 PDF 文件，开始扫描...")

    with get_session(args.workspace, args.config_dir) as session:
        results: list[PdfImportResult] = []
        for pdf_path in pdf_paths:
            print(f"\n[扫描] {pdf_path.name}")
            res = process_pdf_import(
                pdf_path=pdf_path,
                session=session,
                next_id=None,
                pdf_dir=PDF_DIR,
                skip_title_check=args.skip_title_check,
                replace_existing=args.replace_existing,
            )
            if args.dry_run:
                session.rollback()
            results.append(res)
            label = {"ok": "[OK] Crossref 匹配", "fallback": "[INFO] PDF 元数据回退",
                     "mismatch": f"[WARN] {res.error_msg}",
                     "no_doi": "[ERR] 无 DOI", "error": f"[ERR] {res.error_msg}"}.get(_category(res), res.status)
            print(f"  -> {label}")

        print_report(results)

        if args.dry_run:
            print("\n[dry-run 模式，未写入数据库]")
            sys.exit(0)

        if not args.do_import:
            print("\n[提示] 扫描完成。要实际导入，请加上 --import 参数")
            sys.exit(0)

        if not args.yes:
            ok_c = sum(1 for r in results if _category(r) == "ok")
            fallback_c = sum(1 for r in results if _category(r) == "fallback")
            mm_c = sum(1 for r in results if r.status == "mismatch")
            print(f"\n即将导入: {ok_c} 个 Crossref 匹配, {fallback_c} 个 PDF 元数据回退, {mm_c} 个将被跳过")
            if input("确认导入? [y/N]: ").strip().lower() not in ("y", "yes"):
                print("已取消导入")
                sys.exit(0)

        print("\n开始导入...")
        next_id = (session.query(func.max(Paper.internal_id)).scalar() or 0) + 1
        stats = {"success": 0, "skipped": 0, "failed": 0}

        for res in results:
            # The scan pass already writes papers in non-dry-run mode; count those as imported.
            if res.status in ("created", "replaced", "updated"):
                print(f"  [{res.filename}] {res.message}")
                stats["success"] += 1
                continue
            if res.status not in ("ok", "fallback"):
                print(f"  [{res.filename}] 跳过: {res.message}")
                stats["skipped"] += 1
                continue
            import_res = process_pdf_import(
                pdf_path=scan_dir / res.filename,
                session=session,
                next_id=next_id,
                pdf_dir=PDF_DIR,
                skip_title_check=args.skip_title_check,
                replace_existing=args.replace_existing,
            )
            if import_res.status == "error":
                print(f"  [{res.filename}] 失败: {import_res.message}")
                stats["failed"] += 1
            else:
                print(f"  [{res.filename}] {import_res.message}")
                stats["success"] += 1
                if not import_res.existing:
                    next_id += 1

        session.commit()
        print(f"\n导入完成: 成功 {stats['success']}, 跳过 {stats['skipped']}, 失败 {stats['failed']}")
        print("\n提示: 可运行 `python scripts/auto_ingest.py` 补全仍处于 pending 的论文")


if __name__ == "__main__":
    main()
