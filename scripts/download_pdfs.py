#!/usr/bin/env python3
"""
批量 PDF 下载脚本。

扫描待处理/已完成但没有 PDF 的论文，
按优先级链路自动尝试下载，下载成功后自动触发现有导入流程。

配置来源（优先级从高到低）：
  1. 命令行参数 --elsevier-api-key / --unpaywall-email
  2. 环境变量 ELSEVIER_API_KEY / UNPAYWALL_EMAIL
  3. config/runtime.yaml 中的 pdf_download 节
  4. 本地安全默认值

用法示例：
    python scripts/download_pdfs.py --limit 10
    python scripts/download_pdfs.py --doi 10.1016/j.neuroimage.2025.121216
    python scripts/download_pdfs.py --dry-run
    python scripts/download_pdfs.py --source elsevier_only
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.pdf_downloader import PdfDownloader
from paper_library.paper_lifecycle import (
    fail_processing, record_download_error, refresh_pdf_flag,
)
from paper_library.pdf_import import process_pdf_import, PdfImportResult
from tools import storage
from core.config_loader import ConfigLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_config(workspace: str = ".", config_dir: str = "config") -> dict:
    """加载配置，遵循项目配置体系。

    优先级：环境变量 > runtime.yaml > 默认值
    """
    try:
        all_cfg = ConfigLoader(Path(workspace) / config_dir).load_all().get("runtime", {})
    except Exception as e:
        logger.warning("无法加载运行配置，继续使用环境变量: %s", e)
        all_cfg = {}
    base_cfg = all_cfg.get("pdf_download", {}) or {}
    semantic_cfg = all_cfg.get("semantic_scholar", {}) or {}

    return {
        "elsevier_api_key": os.environ.get("ELSEVIER_API_KEY", "") or base_cfg.get("elsevier_api_key", ""),
        "ieee_api_key": os.environ.get("IEEE_API_KEY", "") or base_cfg.get("ieee_api_key", ""),
        "semantic_scholar_api_key": os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "") or semantic_cfg.get("api_key", ""),
        "wiley_tdm_token": os.environ.get("WILEY_TDM_TOKEN", "") or base_cfg.get("wiley_tdm_token", ""),
        "unpaywall_email": os.environ.get("UNPAYWALL_EMAIL", "") or base_cfg.get("unpaywall_email", ""),
        "api_endpoints": all_cfg.get("api_endpoints", {}) or {},
        "request_interval": base_cfg.get("request_interval", 1.0),
    }


def _get_pending_papers(
    session, workspace: str = ".", limit: int | None = None,
    filter_source: str | None = None,
):
    """查询需要下载 PDF 的论文。"""
    stmt = (
        select(Paper)
        .where(Paper.processing_status.in_(["pending", "done", "failed"]))
        .order_by(Paper.created_at.asc())
    )
    if filter_source:
        stmt = stmt.where(Paper.source == filter_source)
    from paper_library.paper_lifecycle import refresh_pdf_flag
    papers = list(session.scalars(stmt).all())
    needing_pdf = []
    for paper in papers:
        refresh_pdf_flag(paper, workspace)
        if not paper.has_pdf:
            needing_pdf.append(paper)
            if limit and len(needing_pdf) >= limit:
                break
    return needing_pdf


def main():
    setup_console()
    parser = argparse.ArgumentParser(description="批量下载论文 PDF")
    add_db_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="只报告，不实际下载")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 篇")
    parser.add_argument("--doi", type=str, default=None, help="指定单个 DOI 下载")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "oa_only", "elsevier_only", "ieee_only"],
        help="限制下载来源。elsevier_only 和 ieee_only 会跳过开放获取来源。",
    )
    parser.add_argument("--workspace", type=str, default=".", help="项目根目录")
    parser.add_argument("--config-dir", type=str, default="config", help="配置目录（默认 config）")
    parser.add_argument("--filter-source", type=str, default=None, help="只处理指定 source 的论文")
    parser.add_argument("--workers", type=int, default=4, help="并行下载线程数（默认 4）")
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    cfg = _load_config(args.workspace, args.config_dir)

    # 根据 --source 调整下载器配置
    skip_oa = False
    platforms = None
    if args.source == "oa_only":
        platforms = ["europepmc", "unpaywall", "semantic_scholar", "arxiv", "springer", "mdpi"]
    elif args.source == "elsevier_only":
        skip_oa = True
        platforms = ["elsevier"]
    elif args.source == "ieee_only":
        skip_oa = True
        platforms = ["ieee"]

    with get_session(args.workspace, args.config_dir) as session:
        if args.doi:
            paper = session.scalar(select(Paper).where(Paper.doi == args.doi.strip()))
            papers = [paper] if paper else []
        else:
            papers = _get_pending_papers(session, args.workspace, args.limit, args.filter_source)

        if not papers:
            print("没有需要下载 PDF 的论文。")
            return

        print(f"共找到 {len(papers)} 篇需要下载 PDF 的论文")
        print(f"并行 workers: {args.workers}\n")

        if args.dry_run:
            print("[DRY-RUN] 只报告，不实际下载")
            for idx, paper in enumerate(papers, 1):
                print(f"[{idx}/{len(papers)}] DOI: {paper.doi} | {paper.title[:60]}...")
            return

        # 先过滤掉已有 PDF 的（避免重复下载）
        ws = Path(args.workspace).resolve()
        papers_to_download = []
        skip_papers = []
        for paper in papers:
            if paper.pdf_storage_path:
                resolved = storage.resolve_storage_path(paper.pdf_storage_path, str(ws))
                if resolved and resolved.exists():
                    skip_papers.append(paper)
                    continue
            papers_to_download.append(paper)

        if skip_papers:
            print(f"跳过 {len(skip_papers)} 篇已有 PDF 的论文")
        if not papers_to_download:
            print("没有需要下载的论文。")
            return

        # 并行下载到临时文件
        results = {}
        completed = 0
        total = len(papers_to_download)

        def _worker(paper):
            """在线程中下载 PDF 到临时文件。"""
            tmp_dir = storage.get_temp_dir(ws) / "downloads"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            norm_doi = paper.doi.replace("/", "_").replace("\\", "_") if paper.doi else f"unknown_{paper.id}"
            tmp_path = tmp_dir / f"{norm_doi}.pdf"

            d = PdfDownloader(
                elsevier_api_key=cfg.get("elsevier_api_key") or None,
                ieee_api_key=cfg.get("ieee_api_key") or None,
                semantic_scholar_api_key=cfg.get("semantic_scholar_api_key") or None,
                wiley_tdm_token=cfg.get("wiley_tdm_token") or None,
                unpaywall_email=cfg.get("unpaywall_email") or None,
                api_endpoints=cfg.get("api_endpoints", {}),
                request_interval=0.5,
            )
            try:
                result = d.download_by_doi(paper.doi, tmp_path, skip_oa=skip_oa, platforms=platforms)
                return paper, result
            finally:
                d.close()

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_worker, p): p for p in papers_to_download}
            for future in as_completed(futures):
                paper, result = future.result()
                completed += 1
                results[paper.id] = (paper, result)
                status = "OK" if result.success else "FAIL"
                print(f"[{completed}/{total}] {status} | DOI: {paper.doi} | {paper.title[:50]}... | {result.method or result.error}")

        # 主线程串行入库（避免 SQLite 并发问题）
        print("\n开始入库...")
        success_count = 0
        fail_count = 0
        skip_count = len(skip_papers)

        pdf_dir = storage.get_pdf_dir(str(ws))
        for paper in papers_to_download:
            paper, result = results[paper.id]
            if not result.success:
                record_download_error(paper)
                fail_count += 1
                continue

            tmp_path = Path(result.path) if result.path else None
            if not tmp_path or not tmp_path.exists():
                record_download_error(paper)
                fail_count += 1
                continue

            import_result = None
            try:
                import_result: PdfImportResult = process_pdf_import(
                    pdf_path=tmp_path,
                    session=session,
                    next_id=paper.internal_id,
                    pdf_dir=pdf_dir,
                    workspace=str(ws),
                )
            except Exception as exc:
                logger.exception("process_pdf_import 异常: %s", exc)
                fail_processing(paper, "pdf_processing_failed")
                fail_count += 1
                print(f"  [IMPORT_FAIL] ID={paper.id} 异常: {exc}")
                continue

            if import_result and import_result.status in ("created", "linked", "replaced", "ok"):
                refresh_pdf_flag(paper, ws)
                success_count += 1
                print(f"  [IMPORTED] ID={paper.id} method={result.method} status={import_result.status}")
            else:
                fail_processing(paper, "pdf_processing_failed")
                fail_count += 1
                status = import_result.status if import_result else "None"
                msg = import_result.message if import_result else "None"
                print(f"  [IMPORT_FAIL] ID={paper.id} status={status} msg={msg}")

            # 清理临时文件
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    logger.warning('处理失败，已跳过该项（main）', exc_info=True)
                    pass

        # 对跳过已有 PDF 的论文刷新文件标志
        for paper in skip_papers:
            refresh_pdf_flag(paper, ws)

        session.commit()

        print("\n" + "=" * 60)
        print("汇总:")
        print(f"  成功下载+入库: {success_count}")
        print(f"  失败: {fail_count}")
        print(f"  跳过(已有PDF): {skip_count}")
        print(f"  总计: {len(papers)}")


if __name__ == "__main__":
    main()
