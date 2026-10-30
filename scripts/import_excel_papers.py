#!/usr/bin/env python3
"""
[历史兼容/当前停用] 从 Web of Science / EndNote 导出的 Excel 文件导入论文到数据库，并自动下载 PDF。

当前生产使用检索或 DOI 新增；本脚本仅用于历史数据恢复，不再作为主流程验收入口。

流程：
  1. 读取 Excel，提取 DOI、标题、期刊、年份、作者、摘要等
  2. 基于 DOI 查询 CrossRef 补全元数据（失败则回退 Excel 数据）
  3. upsert_paper() 入库（source 设为 Excel 文件名，含更新已有记录）
  4. 自动启动 PDF 批量下载

用法：
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --dry-run
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest --no-download
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest --download-limit 50
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select

# ------------------------------------------------------------------
# Path setup
# ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session, get_session_factory
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.crud import upsert_paper, get_paper_by_doi
from paper_library.author_ingest import normalize_authors_for_paper, normalize_author_names_for_paper
from paper_library.doi import normalize_doi
from paper_library.pdf_downloader import PdfDownloader
from core.crossref_api import fetch_metadata_by_doi
from core.config_loader import ConfigLoader, load_yaml_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# CrossRef  polite pool 限流
CROSSREF_DELAY = 0.5
DEFAULT_IMPORT_CONFIG = ROOT / "config" / "imports.yaml"
DEFAULT_COLUMNS = {
    "doi": ["DOI", "doi"],
    "title": ["Article Title", "Title"],
    "journal": ["Source Title", "Journal", "Secondary Title"],
    "year": ["Publication Year", "Year"],
    "authors": ["Authors", "Author"],
    "abstract": ["Abstract"],
    "volume": ["Volume"],
    "issue": ["Issue"],
    "start_page": ["Start Page", "Beginning Page"],
    "end_page": ["End Page"],
    "pages": ["Pages"],
    "pmid": ["Pubmed Id", "PMID"],
    "document_type": ["Document Type", "Type"],
}

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _load_pdf_config(workspace: str = ".", config_dir: str = "config") -> dict:
    """加载 PDF 下载配置（优先级：环境变量 > runtime.yaml > 默认值）"""
    try:
        all_cfg = ConfigLoader(Path(workspace) / config_dir).load_all().get("runtime", {})
    except Exception as e:
        logger.warning("无法加载运行配置，继续使用环境变量: %s", e)
        all_cfg = {}
    base_cfg = all_cfg.get("pdf_download", {}) or {}
    return {
        "elsevier_api_key": os.environ.get("ELSEVIER_API_KEY", "") or base_cfg.get("elsevier_api_key", ""),
        "ieee_api_key": os.environ.get("IEEE_API_KEY", "") or base_cfg.get("ieee_api_key", ""),
        "semantic_scholar_api_key": os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "") or (all_cfg.get("semantic_scholar", {}) or {}).get("api_key", ""),
        "wiley_tdm_token": os.environ.get("WILEY_TDM_TOKEN", "") or base_cfg.get("wiley_tdm_token", ""),
        "springer_api_key": os.environ.get("SPRINGER_API_KEY", "") or (all_cfg.get("springer", {}) or {}).get("api_key", ""),
        "unpaywall_email": os.environ.get("UNPAYWALL_EMAIL", "") or base_cfg.get("unpaywall_email", ""),
        "api_endpoints": all_cfg.get("api_endpoints", {}) or {},
        "request_interval": base_cfg.get("request_interval", 1.0),
    }


def _safe_str(val) -> Optional[str]:
    """将 Excel 值安全转为字符串，空值返回 None"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        logger.warning('处理失败，已跳过该项（_safe_str）', exc_info=True)
        pass
    s = str(val).strip()
    if s.lower() in ("nan", "none", ""):
        return None
    return s


def _safe_int(val) -> Optional[int]:
    """将 Excel 值安全转为整数"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except Exception:
        logger.warning('处理失败，已跳过该项（_safe_int）', exc_info=True)
        pass
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _extract_source_name(excel_path: Path) -> str:
    """从 Excel 路径提取 source 名称（文件名不含扩展名）"""
    return excel_path.stem


def _normalize_excel_doi(doi_val) -> Optional[str]:
    """清洗 Excel 中的 DOI：去掉 URL 前缀，标准化格式"""
    raw = _safe_str(doi_val)
    if not raw:
        return None
    for prefix in [
        "https://doi.org/",
        "http://doi.org/",
        "http://dx.doi.org/",
        "https://dx.doi.org/",
        "doi.org/",
    ]:
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    return normalize_doi(raw)


def _format_pages(start_page, end_page) -> Optional[str]:
    """格式化页码为 start-end"""
    sp = _safe_int(start_page)
    ep = _safe_int(end_page)
    if sp and ep:
        return f"{sp}-{ep}"
    if sp:
        return str(sp)
    if ep:
        return str(ep)
    return None


def load_import_config(config_path: str | Path | None = None) -> dict:
    path = Path(config_path) if config_path else DEFAULT_IMPORT_CONFIG
    data = load_yaml_config(path)
    excel = data.get("excel", {}) or {}
    columns = excel.get("columns", {}) or {}
    return {
        "sheet_name": excel.get("sheet_name") or "Sheet1",
        "columns": {
            field: list(columns.get(field) or candidates)
            for field, candidates in DEFAULT_COLUMNS.items()
        },
    }


def _excel_value(row: pd.Series, columns: dict, field: str):
    for candidate in columns.get(field, DEFAULT_COLUMNS.get(field, [])):
        if candidate in row.index:
            value = row.get(candidate)
            if _safe_str(value) is not None:
                return value
    return None


def _build_paper_data_from_excel(
    row: pd.Series,
    source_name: str,
    row_num: int,
    columns: dict | None = None,
) -> Optional[Dict]:
    """从 Excel 行构建 paper 数据字典"""
    columns = columns or DEFAULT_COLUMNS
    doi = _normalize_excel_doi(_excel_value(row, columns, "doi"))
    title = _safe_str(_excel_value(row, columns, "title"))

    if not title:
        return None

    journal = _safe_str(_excel_value(row, columns, "journal"))
    year = _safe_int(_excel_value(row, columns, "year"))
    authors = _safe_str(_excel_value(row, columns, "authors"))
    abstract = _safe_str(_excel_value(row, columns, "abstract"))
    volume = _safe_str(_excel_value(row, columns, "volume"))
    issue = _safe_str(_excel_value(row, columns, "issue"))
    pages = _safe_str(_excel_value(row, columns, "pages")) or _format_pages(
        _excel_value(row, columns, "start_page"),
        _excel_value(row, columns, "end_page"),
    )
    pmid = _safe_str(_excel_value(row, columns, "pmid"))
    doc_type = _safe_str(_excel_value(row, columns, "document_type"))

    # 原始数据存入 original_data（authors 不能直接传入，因为 Paper.authors 是 relationship）
    original_data: Dict = {}
    if authors:
        original_data["authors"] = authors
    if doc_type:
        original_data["document_type"] = doc_type

    return {
        "doi": doi,
        "title": title,
        "journal_name": journal,
        "year": year,
        "abstract": abstract,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "pmid": pmid,
        "is_in_domain": True,
        "source": source_name,
        "source_excel": source_name,
        "source_row": row_num,
        "original_data": original_data if original_data else None,
    }


def _enrich_from_crossref(doi: str) -> Dict:
    """基于 DOI 从 CrossRef 获取元数据，返回可用于更新 paper_data 的字典"""
    try:
        meta = fetch_metadata_by_doi(doi, structured=True)
        if not meta or meta.get("error") or "_error" in meta:
            return {}

        extra: Dict = {}

        # title
        title = meta.get("title")
        if title:
            extra["title"] = title

        # journal
        journal_info = meta.get("journal") or {}
        if journal_info and journal_info.get("title"):
            extra["journal_name"] = journal_info["title"]

        # year
        year = meta.get("year")
        if year:
            extra["year"] = year

        # authors
        authors_list = meta.get("authors", [])
        if authors_list:
            author_strs = []
            for a in authors_list:
                family = a.get("family", "")
                given = a.get("given", "")
                if family and given:
                    author_strs.append(f"{family}, {given}")
                elif family:
                    author_strs.append(family)
            if author_strs:
                extra["authors"] = "; ".join(author_strs)

        # volume, issue, pages from raw CrossRef payload
        raw = meta.get("raw") or {}
        if raw.get("volume"):
            extra["volume"] = str(raw["volume"])
        if raw.get("issue"):
            extra["issue"] = str(raw["issue"])
        if raw.get("page"):
            extra["pages"] = str(raw["page"])

        # abstract (CrossRef 很少提供，但有就保留)
        if raw.get("abstract"):
            extra["abstract"] = raw["abstract"]

        # 保留原始 CrossRef payload 供下游使用
        extra["doi_meta"] = raw

        return extra
    except Exception as e:
        logger.debug("CrossRef 查询失败 %s: %s", doi, e)
        return {}


# ------------------------------------------------------------------
# Phase 1: Import metadata from Excel
# ------------------------------------------------------------------


def import_excel(
    excel_path: Path,
    session,
    source_name: str,
    dry_run: bool = False,
    skip_existing: bool = False,
    batch_size: int = 50,
    no_crossref: bool = False,
    sheet_name: str | None = None,
    columns: dict | None = None,
) -> Tuple[int, int, int, List[int]]:
    """
    从 Excel 导入论文元数据。

    Returns:
        (processed_count, new_count, updated_count, imported_paper_ids)
    """
    logger.info("📖 读取 Excel: %s", excel_path)
    df = pd.read_excel(excel_path, sheet_name=sheet_name or "Sheet1")
    total_rows = len(df)
    logger.info("   共 %d 行数据", total_rows)

    processed = 0
    new_count = 0
    updated_count = 0
    imported_ids: List[int] = []

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # Excel 行号（1-based + header）

        paper_data = _build_paper_data_from_excel(row, source_name, row_num, columns)
        if not paper_data:
            logger.warning("  [行 %d] 无有效标题，跳过", row_num)
            continue

        doi = paper_data.get("doi")

        # 检查是否已存在
        existing = None
        if doi:
            existing = get_paper_by_doi(session, doi)

        if existing and skip_existing:
            logger.info("  [行 %d] 已存在，跳过: %s", row_num, doi)
            continue

        # CrossRef 补全
        if doi and not no_crossref:
            time.sleep(CROSSREF_DELAY)
            extra = _enrich_from_crossref(doi)
            if extra:
                # 合并策略：CrossRef 数据覆盖 Excel，但保留 Excel 的摘要（CrossRef 通常没有）
                for k, v in extra.items():
                    if k == "abstract" and paper_data.get("abstract"):
                        continue
                    # 跳过 authors，避免覆盖 relationship
                    if k == "authors":
                        continue
                    if v is not None and v != "":
                        paper_data[k] = v
                logger.debug("  [行 %d] CrossRef 补全成功", row_num)
            else:
                logger.debug("  [行 %d] CrossRef 无结果，使用 Excel 数据", row_num)

        if dry_run:
            action = "UPDATE" if existing else "NEW"
            logger.info(
                "  [DRY %s] %s | %s",
                action,
                doi or "(无 DOI)",
                paper_data["title"][:60],
            )
            processed += 1
            if existing:
                updated_count += 1
            else:
                new_count += 1
            continue

        # 入库
        try:
            paper = upsert_paper(session, paper_data)
            if paper.doi_meta:
                try:
                    with session.begin_nested():
                        normalize_authors_for_paper(paper.doi_meta, paper.id, session)
                except Exception as e:
                    logger.warning("  [行 %d] 作者标准化失败: %s", row_num, e)
            else:
                original_data = paper.original_data if isinstance(paper.original_data, dict) else {}
                excel_authors = original_data.get("authors")
                if isinstance(excel_authors, str) and excel_authors.strip():
                    try:
                        with session.begin_nested():
                            normalize_author_names_for_paper(excel_authors, paper.id, session)
                    except Exception as e:
                        logger.warning("  [行 %d] Excel 作者标准化失败: %s", row_num, e)
            imported_ids.append(paper.id)
            processed += 1

            if existing:
                updated_count += 1
                logger.info(
                    "  [UPDATE] 行%d id=%d %s",
                    row_num,
                    paper.id,
                    paper.title[:55],
                )
            else:
                new_count += 1
                logger.info(
                    "  [NEW] 行%d id=%d %s",
                    row_num,
                    paper.id,
                    paper.title[:55],
                )

            if processed % batch_size == 0:
                session.commit()
                logger.info("💾 已提交 %d/%d 条", processed, total_rows)

        except Exception as e:
            logger.error("  [FAIL] 行%d 入库失败: %s", row_num, e)
            session.rollback()

    if not dry_run:
        session.commit()

    return processed, new_count, updated_count, imported_ids


# ------------------------------------------------------------------
# Phase 2: Download PDFs
# ------------------------------------------------------------------


def download_pdfs_for_papers(
    paper_ids: List[int],
    session,
    workspace: str,
    limit: Optional[int] = None,
) -> Tuple[int, int, int]:
    """为指定论文批量下载 PDF"""
    if limit:
        paper_ids = paper_ids[:limit]

    cfg = _load_pdf_config(workspace)
    downloader = PdfDownloader(
        elsevier_api_key=cfg["elsevier_api_key"] or None,
        ieee_api_key=cfg["ieee_api_key"] or None,
        semantic_scholar_api_key=cfg["semantic_scholar_api_key"] or None,
        wiley_tdm_token=cfg["wiley_tdm_token"] or None,
        unpaywall_email=cfg["unpaywall_email"] or None,
        api_endpoints=cfg["api_endpoints"],
        request_interval=cfg["request_interval"],
    )

    papers = list(session.scalars(select(Paper).where(Paper.id.in_(paper_ids))).all())
    papers.sort(key=lambda p: p.id)

    success = 0
    fail = 0
    skip = 0

    logger.info("")
    logger.info("=" * 60)
    logger.info("📥 开始下载 PDF，共 %d 篇", len(papers))
    logger.info("=" * 60)

    for idx, paper in enumerate(papers, 1):
        prefix = f"[{idx}/{len(papers)}]"
        if not paper.doi:
            logger.info("%s 无 DOI，跳过: %s", prefix, paper.title[:50])
            skip += 1
            continue

        logger.info("%s %s | %s", prefix, paper.doi, paper.title[:50])

        try:
            result = downloader.download_for_paper(paper, session, workspace=workspace)
            session.commit()
        except Exception as e:
            logger.error("  [FAIL] 异常: %s", e)
            session.rollback()
            fail += 1
            continue

        if result.success:
            if result.method == "existing":
                logger.info("  [SKIP] PDF 已存在")
                skip += 1
            else:
                logger.info("  [OK] %s -> %s", result.method, result.path)
                success += 1
        else:
            logger.info("  [FAIL] %s", result.error)
            fail += 1

    logger.info("=" * 60)
    logger.info("📊 PDF 下载汇总")
    logger.info("  成功: %d", success)
    logger.info("  失败: %d", fail)
    logger.info("  跳过: %d", skip)
    logger.info("  总计: %d", len(papers))
    logger.info("=" * 60)

    return success, fail, skip


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------


def main():
    setup_console()
    parser = argparse.ArgumentParser(
        description="[历史兼容/当前停用] 从 Excel 导入论文并自动下载 PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --dry-run
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest --no-download
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest --download-limit 50
  python scripts/import_excel_papers.py data/excel/savedrecs.xlsx --auto-ingest --skip-existing
        """,
    )
    add_db_arguments(parser)
    parser.add_argument("excel_path", type=Path, help="Excel 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不入库")
    parser.add_argument("--auto-ingest", action="store_true", help="跳过确认直接入库")
    parser.add_argument("--no-download", action="store_true", help="入库后不自动下载 PDF")
    parser.add_argument(
        "--download-limit", type=int, default=None, help="PDF 下载数量限制"
    )
    parser.add_argument(
        "--skip-existing", action="store_true", help="跳过数据库中已存在的 DOI"
    )
    parser.add_argument(
        "--no-crossref", action="store_true", help="跳过 CrossRef 元数据补全"
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, help="入库批次大小 (默认 50)"
    )
    parser.add_argument(
        "--workspace", type=str, default=str(ROOT), help="项目根目录"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_IMPORT_CONFIG, help="Excel 导入配置文件")
    parser.add_argument("--sheet", default=None, help="覆盖配置中的工作表名称")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    if not args.excel_path.exists():
        logger.error("❌ 文件不存在: %s", args.excel_path)
        sys.exit(1)

    source_name = _extract_source_name(args.excel_path)
    import_config = load_import_config(args.config)
    sheet_name = args.sheet or import_config["sheet_name"]
    logger.info("📁 Source 名称: %s", source_name)
    logger.info("📁 工作目录: %s", args.workspace)

    # ------------------------------------------------------------------
    # Phase 1: Import metadata
    # ------------------------------------------------------------------
    with get_session(args.workspace) as session:
        processed, new_count, updated_count, imported_ids = import_excel(
            excel_path=args.excel_path,
            session=session,
            source_name=source_name,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
            batch_size=args.batch_size,
            no_crossref=args.no_crossref,
            sheet_name=sheet_name,
            columns=import_config["columns"],
        )

    logger.info("")
    logger.info("=" * 60)
    if args.dry_run:
        logger.info("📋 [DRY-RUN] 预览完成")
    else:
        logger.info("✅ 元数据入库完成")
    logger.info("  处理: %d", processed)
    logger.info("  新增: %d", new_count)
    logger.info("  更新: %d", updated_count)
    logger.info("  涉及论文 ID: %d 个", len(imported_ids))
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY-RUN 模式，未写入数据库，跳过 PDF 下载")
        return

    if args.no_download:
        logger.info("--no-download 指定，跳过 PDF 下载")
        logger.info("后续可运行: python scripts/download_pdfs.py")
        return

    if not imported_ids:
        logger.info("无论文需要处理，跳过 PDF 下载")
        return

    # ------------------------------------------------------------------
    # Phase 2: Download PDFs
    # ------------------------------------------------------------------
    session_factory = get_session_factory(args.workspace)
    download_session = session_factory()
    try:
        download_pdfs_for_papers(
            paper_ids=imported_ids,
            session=download_session,
            workspace=args.workspace,
            limit=args.download_limit,
        )
    finally:
        download_session.close()


if __name__ == "__main__":
    main()
