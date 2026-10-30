"""
PDF 导入核心逻辑模块。

提供从 PDF 提取 DOI、查询 CrossRef、标题校验、创建/更新 Paper 记录的完整流程。
被 CLI 脚本和 FastAPI 端点共用。
"""

from __future__ import annotations

import difflib
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from paper_library.abstract_enrichment import enrich_missing_abstract, extract_crossref_abstract
from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.crud import upsert_paper, get_paper_by_doi
from paper_library.doi import normalize_doi
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal
from paper_library.models import Paper
from paper_library.paper_lifecycle import (
    complete_processing,
    fail_processing,
    refresh_metadata_complete,
    refresh_pdf_flag,
    start_processing,
)
from core.crossref_api import clean_crossref_abstract, extract_doi_from_text, fetch_metadata_by_doi
from core.pdf_extractor import extract_text_and_images
from core.date_extractor import enrich_paper_dates
from paper_library.citation import sanitize_scholarly_markup
from tools.storage import get_paper_image_dir

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────
PDF_TEXT_LIMIT = 10000
TITLE_SEARCH_CHARS = 3000
SIMILARITY_THRESHOLD = 0.65
MAX_PAGES_FOR_TEXT = 5

# ── 共享 JCR 匹配器（懒加载）──────────────────────────────────
_jcr_matcher: Optional[JcrMatcher] = None
_jcr_matcher_workspace: Optional[Path] = None


def _get_jcr_matcher(workspace: str | Path = ".") -> Optional[JcrMatcher]:
    """获取共享的参考数据匹配器实例。"""
    global _jcr_matcher, _jcr_matcher_workspace
    configured_workspace = Path(workspace).resolve()
    if _jcr_matcher is None or _jcr_matcher_workspace != configured_workspace:
        _jcr_matcher = JcrMatcher(workspace=configured_workspace)
        _jcr_matcher_workspace = configured_workspace
        _jcr_matcher.load()
    return _jcr_matcher


# ── 数据类 ────────────────────────────────────────────────────

@dataclass
class PdfImportResult:
    """单个 PDF 的处理结果"""
    filename: str = ""
    status: str = "pending"   # created / linked / replaced / existing_with_pdf / mismatch / no_doi / error
    doi: Optional[str] = None
    paper_id: Optional[int] = None
    internal_id: Optional[int] = None
    title: Optional[str] = None
    pdf_title: str = ""
    similarity: float = 0.0
    crossref_year: Optional[int] = None
    crossref_journal: Optional[str] = None
    message: str = ""
    error_msg: str = ""
    existing: bool = False
    has_existing_pdf: bool = False
    fallback: bool = False


# ── PDF 文本提取 ──────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path, max_chars: int = PDF_TEXT_LIMIT) -> str:
    """从 PDF 提取前 N 页文本，返回前 max_chars 个字符。"""
    text_chunks: List[str] = []
    try:
        with pymupdf.open(pdf_path) as doc:
            for page_idx, page in enumerate(doc):
                if page_idx >= MAX_PAGES_FOR_TEXT:
                    break
                text_chunks.append(page.get_text("text"))
                if sum(len(t) for t in text_chunks) >= max_chars:
                    break
    except Exception:
        logger.warning("PDF text extraction failed", exc_info=True)
        return ""

    full_text = "\n".join(text_chunks).strip()
    return full_text[:max_chars]


def extract_pdf_metadata(pdf_path: Path, text: str = "") -> Dict[str, Any]:
    """从 PDF 元数据及首页文本中提取回退入库所需字段。"""
    metadata: Dict[str, Any] = {}
    first_page_title = ""
    pdf_metadata: Dict[str, Any] = {}
    valid_pdf = False
    try:
        with pymupdf.open(pdf_path) as doc:
            valid_pdf = True
            pdf_metadata = doc.metadata or {}
            if doc.page_count:
                page = doc[0]
                line_candidates: List[tuple[float, int, str]] = []
                for block in page.get_text("dict").get("blocks", []):
                    for line in block.get("lines", []):
                        spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
                        line_text = "".join(span.get("text", "") for span in spans).strip()
                        if len(line_text) < 10:
                            continue
                        max_size = max((float(span.get("size", 0)) for span in spans), default=0.0)
                        line_candidates.append((max_size, len(line_text), line_text))
                if line_candidates:
                    first_page_title = max(line_candidates, key=lambda item: (item[0], item[1]))[2]
    except Exception:
        logger.warning('处理失败，已跳过该项（extract_pdf_metadata）', exc_info=True)
        pass

    metadata["_valid_pdf"] = valid_pdf
    title = _clean_pdf_title(pdf_metadata.get("title")) or _clean_pdf_title(first_page_title)
    if title:
        metadata["title"] = title

    author_value = str(pdf_metadata.get("author") or "").strip()
    if author_value:
        metadata["authors"] = [
            name.strip()
            for name in re.split(r"\s*;\s*|\s+and\s+|、", author_value, flags=re.IGNORECASE)
            if name.strip()
        ]

    for date_key in ("creationDate", "modDate"):
        year = _extract_year(str(pdf_metadata.get(date_key) or ""))
        if year:
            metadata["year"] = year
            break
    if not metadata.get("year"):
        metadata["year"] = _extract_year(text)

    abstract = extract_pdf_abstract(text)
    if abstract:
        metadata["abstract"] = clean_crossref_abstract(abstract) or abstract
    return metadata


def _clean_pdf_title(value: Any) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if not 5 <= len(title) <= 300:
        return ""
    if re.match(r"^(untitled|microsoft word|adobe acrobat|scan\s*\d*)$", title, re.I):
        return ""
    return title


def _extract_year(value: str) -> Optional[int]:
    match = re.search(r"(?<!\d)(?:19|20)\d{2}(?!\d)", value or "")
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= 2099 else None


def extract_pdf_abstract(text: str) -> str:
    """提取首页附近 Abstract 标题后的首段摘要。"""
    if not text:
        return ""
    lines = text[:TITLE_SEARCH_CHARS].splitlines()
    start = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*abstract\b\s*:?\s*(.*)$", line, re.I)
        if match:
            start = index
            first_content = match.group(1).strip()
            break
    if start is None:
        return ""

    paragraph = [first_content] if first_content else []
    stop_heading = re.compile(
        r"^\s*(?:keywords?|index terms|\d+\.?\s+introduction|introduction|background)\b",
        re.I,
    )
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stop_heading.match(stripped):
            break
        paragraph.append(stripped)
    return re.sub(r"\s+", " ", " ".join(paragraph)).strip()


# ── 候选标题提取 ──────────────────────────────────────────────

def extract_candidate_title_from_pdf(text: str) -> List[str]:
    """
    从 PDF 文本中提取候选标题列表。

    策略：在前 25 行过滤垃圾行后，尝试合并连续行生成候选标题。
    返回所有候选（按长度过滤后的列表）。
    """
    if not text:
        return []

    search_text = text[:TITLE_SEARCH_CHARS]
    lines = [ln.strip() for ln in search_text.splitlines() if ln.strip()]

    def _is_junk(ln: str) -> bool:
        lower = ln.lower()
        if re.search(r"\b10\.\d{4,9}/", lower):
            return True
        if ln.startswith("http") or "@" in ln:
            return True
        if len(ln) < 10:
            return True
        if re.match(r"^\d+$", ln):
            return True
        if re.search(r"\(\d{4}\)", ln) and (
            re.search(r"\bet\s+al\.\b", ln, re.I) or
            re.search(r"\d+:\d+", ln)
        ):
            return True
        if len(re.findall(r",", ln)) >= 3 and re.search(r"[\d†*#]", ln):
            return True
        if re.match(r"^\d+[A-Z]", ln):
            return True
        skip_patterns = [
            r"^(Volume|Issue|Page|Received|Accepted|Published|Copyright|All rights reserved|License|This is an?|This article|Supplementary|Acknowledgements?|Funding|Conflict|Author|Correspond|Email|Abstract|Background|Introduction|Methods|Results|Discussion|Conclusion|Keywords|Reference|Study protocol|Open Access|RESEARCH|ORIGINAL|REVIEW|CASE REPORT|EDITORIAL|LETTER)",
            r"^(Downloaded from|https?://|doi\.org|arXiv|medRxiv|bioRxiv|Published by|PMCID|PMID|eISSN|ISSN)",
            r"\b(CC BY|Creative Commons|open access|preprint|under review|all rights|licence|license|permit|statutory|regulation|permission|copyright holder)\b",
            r"\b(author\(s\)\s+202|original author|credit line|material is not included)\b",
            r"^(TYPE\s+Study\s+Protocol|PUBLISHED|EDITED\s+BY|REVIEWED\s+BY|CORRESPONDENCE|CITATION)",
        ]
        return any(re.search(p, ln, re.I) for p in skip_patterns)

    filtered = [ln for ln in lines if not _is_junk(ln)]
    if not filtered:
        return []

    top = filtered[:25]
    candidates: List[str] = []
    i = 0
    while i < len(top):
        candidates.append(top[i])
        merged = top[i]
        j = i + 1
        while j < len(top):
            if merged[-1] in ".?!":
                break
            if len(top[j]) < 15:
                break
            merged = merged + " " + top[j]
            candidates.append(merged)
            j += 1
        i += 1

    candidates = [c for c in candidates if 30 <= len(c) <= 300]
    return candidates


# ── 标题相似度 ────────────────────────────────────────────────

def title_similarity(a: str, b: str) -> float:
    """计算两个标题的相似度 (0.0 ~ 1.0)。"""
    if not a or not b:
        return 0.0

    def norm(s: str) -> str:
        s = s.lower()
        s = re.sub(r"[^\w\s]", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ── PDF 文件名生成 ────────────────────────────────────────────

def make_pdf_filename(paper_id: int, internal_id: Optional[int], year: Optional[int], title: str) -> str:
    """生成规范的 PDF 文件名。

    格式: {internal_id:04d}_{paper_id}_{year}_{snippet}.pdf
      - 已分配编号的论文按 internal_id 排在前面
      - 未分配编号的论文用 9999 占位，排在后面
    """
    snippet = title[:80] if title else "paper"
    snippet = re.sub(r"[^\w\s-]", "", snippet)
    snippet = re.sub(r"\s+", "_", snippet.strip())
    snippet = snippet[:60]
    snippet = snippet.strip("_")
    year_part = str(year) if year else "UnknownYear"
    id_part = f"{internal_id:04d}" if internal_id is not None else "9999"
    return f"{id_part}_{paper_id}_{year_part}_{snippet}.pdf"


# ── Paper 数据构建 ────────────────────────────────────────────

def build_paper_data(
    doi: str,
    title: str,
    year: Optional[int],
    journal_name: Optional[str],
    crossref_raw: Optional[Dict],
    internal_id: Optional[int],
) -> Dict[str, Any]:
    """根据扫描结果构建 Paper 数据字典。"""
    paper_data: Dict[str, Any] = {
        "doi": doi,
        "title": title or "(无标题)",
        "year": year,
        "journal_name": journal_name,
        "doi_meta": crossref_raw,
        "source": "pdf_import",
    }
    if internal_id is not None:
        paper_data["internal_id"] = internal_id
    if crossref_raw:
        if crossref_raw.get("abstract"):
            paper_data["abstract"] = crossref_raw["abstract"]
        if crossref_raw.get("volume"):
            paper_data["volume"] = str(crossref_raw["volume"])
        if crossref_raw.get("issue"):
            paper_data["issue"] = str(crossref_raw["issue"])
        if crossref_raw.get("page"):
            paper_data["pages"] = str(crossref_raw["page"])
    return paper_data


# ── 核心处理函数 ──────────────────────────────────────────────

def _resolve_storage_path(storage_path: str | None, workspace: Path) -> Path | None:
    """将数据库中的存储路径（相对或绝对）解析为绝对路径。"""
    if not storage_path:
        return None
    p = Path(storage_path)
    if p.is_absolute():
        return p
    return (workspace / p).resolve()


def _relative_storage_path(abs_path: Path, workspace: Path) -> str:
    """将绝对路径转为相对于 workspace 的路径（使用正斜杠）。"""
    try:
        return str(abs_path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(abs_path).replace("\\", "/")


def process_pdf_import(
    pdf_path: Path,
    session: Session,
    next_id: Optional[int],
    pdf_dir: Path,
    skip_title_check: bool = False,
    replace_existing: bool = False,
    workspace: str = ".",
    expected_doi: Optional[str] = None,
) -> PdfImportResult:
    """
    处理单个 PDF 导入。

    三种场景：
      1. DOI 不存在 → 创建新 Paper 记录 + 归档 PDF
      2. DOI 存在但无 PDF → 直接归档 PDF 并链接到已有记录
      3. DOI 存在且有 PDF → 若 replace_existing=True 则覆盖，否则标记为 existing_with_pdf

    Args:
        pdf_path: PDF 文件路径
        session: SQLAlchemy session
        next_id: 为新增论文分配的 internal_id（None 表示不分配）
        pdf_dir: PDF 归档目录
        skip_title_check: 是否跳过标题相似度检查
        replace_existing: 是否覆盖已有 PDF
        workspace: 项目根目录（用于相对路径解析）

    Returns:
        PdfImportResult
    """
    ws = Path(workspace).resolve()
    res = PdfImportResult(filename=pdf_path.name)

    # 1. 提取文本
    pdf_text = extract_pdf_text(pdf_path)
    pdf_metadata = extract_pdf_metadata(pdf_path, pdf_text)
    if not pdf_text and not pdf_metadata.get("_valid_pdf"):
        res.status = "error"
        res.error_msg = "无法读取 PDF 文件"
        res.message = res.error_msg
        return res

    # 2. 提取 DOI（优先使用传入的 expected_doi，避免 PDF 中引用文献的 DOI 误匹配）
    if expected_doi:
        raw_doi = expected_doi
    else:
        raw_doi = extract_doi_from_text(pdf_text)
    normalized = normalize_doi(raw_doi) if raw_doi else None
    if raw_doi and not normalized and expected_doi:
        res.status = "error"
        res.error_msg = f"DOI 格式无效: {raw_doi}"
        res.message = res.error_msg
        return res
    res.doi = normalized

    # 3. 查询 CrossRef before opening a database transaction. 无 DOI、无响应
    # 或无匹配时使用 PDF 自身元数据。
    meta = None
    if normalized:
        try:
            time.sleep(0.5)
            meta = fetch_metadata_by_doi(normalized, structured=True)
        except Exception as e:
            logger.info("Crossref 查询异常，回退到 PDF 元数据: %s", e)
    crossref_ok = bool(meta and not meta.get("_error") and not meta.get("error"))

    # 4. 网络调用结束后再查数据库，避免等待 CrossRef 时持有连接/事务。
    existing_paper = get_paper_by_doi(session, normalized) if normalized else None
    res.existing = existing_paper is not None

    if crossref_ok:
        crossref_title = meta.get("title")
        crossref_year = meta.get("year")
        raw = meta.get("raw") or {}
        crossref_journal = (raw.get("container-title") or [None])[0]
    else:
        fallback_title = pdf_metadata.get("title")
        if not fallback_title:
            candidates = extract_candidate_title_from_pdf(pdf_text)
            fallback_title = candidates[0] if candidates else ""
        crossref_title = fallback_title or "(无标题)"
        crossref_year = pdf_metadata.get("year")
        crossref_journal = None
        raw = {}
        res.fallback = True
        res.status = "fallback"
        res.title = crossref_title
        res.crossref_year = crossref_year
        res.pdf_title = fallback_title or ""
        res.message = "Crossref 未匹配，使用 PDF 元数据回退入库"

    if crossref_ok:
        res.title = crossref_title
        res.crossref_year = crossref_year
        res.crossref_journal = crossref_journal

    # 5. 提取 PDF 候选标题并对比
    candidates = extract_candidate_title_from_pdf(pdf_text) if crossref_ok else []
    best_candidate = None
    best_sim = 0.0

    if crossref_ok and crossref_title and candidates:
        for cand in candidates:
            sim = title_similarity(crossref_title, cand)
            if sim > best_sim:
                best_sim = sim
                best_candidate = cand

    if crossref_ok:
        res.pdf_title = best_candidate or ""
    res.similarity = best_sim

    if crossref_ok:
        if best_candidate:
            if best_sim >= SIMILARITY_THRESHOLD:
                res.status = "ok"
            else:
                # 关键词回退
                keywords = crossref_title.lower().split()[:10] if crossref_title else []
                pdf_lower = pdf_text.lower()
                matches = sum(1 for kw in keywords if kw in pdf_lower)
                keyword_ratio = matches / len(keywords) if keywords else 0
                if keyword_ratio >= 0.7:
                    res.status = "ok"
                else:
                    res.status = "mismatch"
                    res.error_msg = f"标题不匹配 (相似度 {best_sim:.2f})"
        else:
            if crossref_title:
                keywords = crossref_title.lower().split()[:10]
                pdf_lower = pdf_text.lower()
                matches = sum(1 for kw in keywords if kw in pdf_lower)
                if matches / len(keywords) >= 0.7:
                    res.status = "ok"
                else:
                    res.status = "mismatch"
                    res.error_msg = "无法从 PDF 提取标题，且关键词不匹配"
            else:
                res.status = "ok"

    # 6. 若状态为 error/no_doi/mismatch（且未强制），直接返回
    if res.status == "error":
        res.message = res.error_msg
        return res
    if res.status == "no_doi":
        res.message = res.error_msg
        return res
    if res.status == "mismatch" and not skip_title_check:
        res.message = res.error_msg
        return res

    # 7. 创建/更新 Paper 记录
    if existing_paper:
        paper = existing_paper
        if res.fallback:
            if not paper.title or paper.title == "(无标题)":
                paper.title = sanitize_scholarly_markup(crossref_title or paper.title)
            if paper.year is None:
                paper.year = crossref_year
            if not paper.abstract:
                paper.abstract = pdf_metadata.get("abstract")
            if not paper.original_data and pdf_metadata.get("authors"):
                paper.original_data = {"authors": pdf_metadata["authors"]}
        else:
            # 不再自动分配 internal_id：有则保留，无则保持 None。
            # 更新 CrossRef 元数据（如果之前没有）
            if not paper.title or paper.title == "(无标题)":
                paper.title = crossref_title or paper.title
            if not paper.year:
                paper.year = crossref_year
            if not paper.journal_name:
                paper.journal_name = crossref_journal
            if not paper.doi_meta:
                paper.doi_meta = raw
            if not paper.abstract:
                paper.abstract = extract_crossref_abstract(raw)

        # 检查是否已有 PDF
        existing_pdf_path = _resolve_storage_path(paper.pdf_storage_path, ws)
        has_pdf = bool(existing_pdf_path and existing_pdf_path.exists())
        res.has_existing_pdf = has_pdf

        if has_pdf and not replace_existing:
            res.status = "existing_with_pdf"
            res.message = "该论文已有 PDF，如需覆盖请设置 replace_existing=true"
            res.paper_id = paper.id
            res.internal_id = paper.internal_id
            return res
    elif res.fallback:
        fallback_title = crossref_title or "(无标题)"
        authors = pdf_metadata.get("authors") or []
        original_data = {"authors": authors} if authors else None
        paper_data = {
            "doi": normalized,
            "title": sanitize_scholarly_markup(fallback_title),
            "year": crossref_year,
            "journal_name": None,
            "abstract": pdf_metadata.get("abstract"),
            "doi_meta": None,
            "source": "pdf_metadata",
            "original_data": original_data,
        }
        if next_id is not None:
            paper_data["internal_id"] = next_id
        if not normalized:
            stmt = select(Paper).where(Paper.title.ilike(paper_data["title"].strip()))
            paper = session.scalar(stmt)
            if paper:
                res.existing = True
        else:
            paper = None
        if paper is None:
            paper = Paper(**paper_data)
            session.add(paper)
            session.flush()
        elif not paper.original_data and original_data:
            paper.original_data = original_data
        if not paper.source:
            paper.source = "pdf_metadata"
        if not res.existing:
            paper.source = "pdf_metadata"
    else:
        paper_data = build_paper_data(
            doi=normalized,
            title=crossref_title or "",
            year=crossref_year,
            journal_name=crossref_journal,
            crossref_raw=raw,
            internal_id=next_id,
        )
        paper = upsert_paper(session, paper_data)

    if paper.doi_meta:
        try:
            with session.begin_nested():
                normalize_authors_for_paper(paper.doi_meta, paper.id, session)
        except Exception:
            logger.warning(
                "作者标准化失败 paper_id=%s doi=%s",
                paper.id,
                paper.doi,
                exc_info=True,
            )

    res.paper_id = paper.id
    res.internal_id = paper.internal_id

    # 8. 归档 PDF
    pdf_dir.mkdir(parents=True, exist_ok=True)
    new_filename = make_pdf_filename(
        paper.id,
        paper.internal_id,
        crossref_year or paper.year,
        crossref_title or paper.title or "paper"
    )
    dest_path = pdf_dir / new_filename

    # 如果已有 PDF 且允许覆盖，先删除旧文件（避免生成 _1 后缀）
    old_path = _resolve_storage_path(paper.pdf_storage_path, ws)
    if res.existing and replace_existing and old_path and old_path.exists():
        # 避免删除与源文件相同的文件
        if pdf_path.resolve() != old_path.resolve():
            try:
                old_path.unlink()
            except Exception:
                logger.warning('处理失败，已跳过该项（process_pdf_import）', exc_info=True)
                pass

    # 如果允许覆盖且目标文件就是旧文件，直接覆盖（不生成 _1 后缀）
    if replace_existing and old_path and dest_path.resolve() == old_path.resolve():
        pass  # 直接使用 dest_path，无需递增
    else:
        counter = 1
        original_dest = dest_path
        while dest_path.exists():
            stem = original_dest.stem
            dest_path = pdf_dir / f"{stem}_{counter}.pdf"
            counter += 1

    try:
        # 如果源文件和目标文件相同（已在正确位置），跳过复制
        if pdf_path.resolve() != dest_path.resolve():
            shutil.copy2(pdf_path, dest_path)
        paper.pdf_storage_path = _relative_storage_path(dest_path.resolve(), ws)
        start_processing(paper)
        refresh_pdf_flag(paper, ws)
        session.flush()

        # 设置最终状态
        if not res.existing:
            res.status = "created"
            res.message = f"created: {paper.title[:60]}..."
        elif replace_existing and res.has_existing_pdf:
            res.status = "replaced"
            res.message = f"replaced (PDF 覆盖): {paper.title[:60]}..."
        else:
            res.status = "linked"
            res.message = f"linked (PDF 链接到已有论文): {paper.title[:60]}..."

        # ── 自动提取 PDF 文本和图片 ────────────────────────────
        try:
            image_output_dir = get_paper_image_dir(paper.id, workspace)
            text, images = extract_text_and_images(str(dest_path), image_output_dir)
            if text or images:
                paper.extracted_text = text
                paper.extracted_images = images
                paper.pdf_text_extracted = bool(text)
                if text:
                    enrich_missing_abstract(paper, refresh_crossref=False)
                complete_processing(paper)
                session.flush()
            else:
                paper.pdf_text_extracted = False
                fail_processing(paper, "pdf_processing_failed")
                res.status = "error"
                res.error_msg = "PDF 文本和图片提取均为空"
                res.message = res.error_msg
                session.flush()
        except Exception:
            # 提取失败不阻断导入流程
            paper.pdf_text_extracted = False
            fail_processing(paper, "pdf_processing_failed")
            res.status = "error"
            res.error_msg = "PDF 内容提取失败"
            res.message = res.error_msg
            session.flush()

        # ── 自动提取接收/发表日期 ────────────────────────────────
        try:
            if paper.processing_status == "done":
                enrich_paper_dates(paper)
                session.flush()
        except Exception:
            # 日期提取失败不阻断导入流程
            logger.debug('可选步骤失败，继续执行（process_pdf_import）', exc_info=True)
            pass

        refresh_pdf_flag(paper, ws)
        refresh_metadata_complete(paper)

        # ── 自动补全期刊信息 ────────────────────────────────────
        try:
            matcher = _get_jcr_matcher(workspace)
            if matcher:
                enrich_result = enrich_paper_journal(paper, session, matcher)
                if enrich_result["status"] == "matched":
                    details = enrich_result.get("details", {})
                    if details:
                        res.message += f" [期刊补全: {', '.join(f'{k}={v}' for k, v in details.items())}]"
                    session.flush()
        except Exception:
            # 期刊补全失败不阻断导入流程
            logger.debug('可选步骤失败，继续执行（process_pdf_import）', exc_info=True)
            pass

    except Exception:
        logger.exception("PDF archive copy failed for paper %s", res.paper_id)
        session.rollback()
        fail_processing(paper, "pdf_archive_failed")
        res.status = "error"
        res.error_msg = "PDF 归档失败，请稍后重试"
        res.message = res.error_msg

    return res
