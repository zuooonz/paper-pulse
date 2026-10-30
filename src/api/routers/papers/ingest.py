"""Paper ingest API routes."""
import logging

from fastapi import APIRouter

from typing import Any
from core.config_loader import ConfigLoader
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from typing import List
from core.llm_client import OpenAICompatibleClient
from typing import Optional
from paper_library.models import Paper
from pathlib import Path
from paper_library.pdf_downloader import PdfDownloader
from fastapi import Query
from fastapi import Request
from fastapi import UploadFile
from core.entity_detector import detect_entity
from paper_library.abstract_enrichment import enrich_missing_abstract
from core.date_extractor import enrich_paper_dates
from paper_library.journal_enrichment import enrich_paper_journal
from paper_library.abstract_enrichment import extract_crossref_abstract
from core.pdf_extractor import extract_text_and_images
from core.crossref_api import fetch_metadata_by_doi
from ...dependencies import get_current_user
from paper_library.crud import get_paper_by_doi
from paper_library.db import get_session
from core.entity_detector import get_target_entity_key
from core.entity_detector import get_target_label
from core.entity_detector import load_entities_config
from core.config_loader import load_yaml_config
from paper_library.paper_lifecycle import (
    complete_processing, fail_processing, record_download_error,
    refresh_metadata_complete, refresh_pdf_flag, start_processing,
)
from paper_library.abstract_enrichment import needs_chinese_abstract_package
from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.doi import normalize_doi
import os
from paper_library.pdf_import import process_pdf_import
from ...dependencies import require_reviewer
from ...dependencies import require_uploader
from ...config import settings
from ...error_responses import log_internal_error
from tools import storage
import tempfile
import time
from paper_library.abstract_enrichment import translate_abstract_to_chinese
from paper_library.crud import upsert_paper
from paper_library.time_utils import utc_now
from tools.upload_limits import (
    PDF_MAX_UPLOAD_BYTES,
    UploadTooLarge,
    copy_upload,
    copy_upload_to_path,
    format_upload_limit,
    validate_upload_size,
)

from .schemas import (
    BatchDoiRequest,
)
from .common import _get_jcr_matcher, ensure_paper_visible

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_BATCH_PDF_FILES = 10

_DOI_ENTITY_TEXT_FIELDS = {
    "abstract",
    "ack",
    "acknowledgement",
    "acknowledgements",
    "acknowledgment",
    "acknowledgments",
    "alternative-title",
    "award",
    "description",
    "funding",
    "funding-group",
    "funding-reference",
    "funder",
    "subject",
    "subtitle",
    "title",
}

def _doi_import_mark_as_target(config_dir: Path | None = None) -> bool:
    """Read the DOI import target-marking option, with its environment override."""
    env_value = os.environ.get("DOI_IMPORT_MARK_AS_TARGET")
    if env_value is not None:
        return env_value.strip().lower() in {"1", "true", "yes", "on"}

    imports_path = (config_dir or (Path(settings.workspace) / settings.config_dir)) / "imports.yaml"
    doi_config = load_yaml_config(imports_path).get("doi") or {}
    configured_value = doi_config.get("mark_as_target", False) if isinstance(doi_config, dict) else False
    if isinstance(configured_value, bool):
        return configured_value
    if isinstance(configured_value, (int, float)):
        return bool(configured_value)
    return str(configured_value).strip().lower() in {"1", "true", "yes", "on"}

def _doi_metadata_text(metadata: dict[str, Any]) -> str:
    """Collect descriptive Crossref fields that can carry entity mentions."""
    text_parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                text_parts.append(cleaned)
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    def collect_fields(payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        for key, value in payload.items():
            normalized_key = str(key).lower().replace("_", "-")
            if normalized_key in _DOI_ENTITY_TEXT_FIELDS:
                collect(value)

    collect_fields(metadata)
    collect_fields(metadata.get("raw"))
    abstract = extract_crossref_abstract(metadata)
    if abstract:
        text_parts.append(abstract)
    return "\n".join(text_parts)

def _doi_import_entity_fields(
    metadata: dict[str, Any],
    *,
    mark_as_target: bool,
    entities_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回 DOI 导入时需要应用的实体字段。"""
    if mark_as_target:
        target_label = (
            get_target_label(config=entities_config)
            if entities_config is not None
            else get_target_label()
        )
        return {"entity_label": target_label, "entity_source": "doi_import"}

    text = _doi_metadata_text(metadata)
    if not text:
        return {}

    config = entities_config if entities_config is not None else load_entities_config()
    entity_label, confidence, scores = detect_entity(text, config=config)
    if not entity_label:
        return {}

    fields: dict[str, Any] = {
        "entity_label": entity_label,
        "entity_source": "doi_metadata",
        "entity_confidence": confidence,
    }
    target_key = get_target_entity_key(config=config)
    if target_key and scores.get(target_key):
        fields["cites_target"] = True
    return fields

@router.post("/batch-doi")
def batch_doi_upload(
    req: BatchDoiRequest,
    user: dict = Depends(require_uploader),
):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        results: List[dict] = []
        mark_doi_import_as_target = _doi_import_mark_as_target()
        matcher = _get_jcr_matcher()
        translation_client = None
        translation_model = "example-chat-model"
        if req.translate_zh:
            try:
                loaded = ConfigLoader(
                    Path(settings.workspace).resolve() / settings.config_dir
                ).load_all()
                provider = loaded.get("runtime", {}).get("provider", {}) or {}
                abstract_cfg = loaded.get("runtime", {}).get("abstract_enrichment", {}) or {}
                translation_model = abstract_cfg.get("model") or translation_model
                candidate = OpenAICompatibleClient(
                    base_url=provider.get("base_url", ""),
                    api_key=provider.get("api_key", ""),
                    timeout_seconds=300,
                    max_retries=3,
                )
                if candidate.available():
                    translation_client = candidate
            except Exception as exc:
                logger.warning("DOI import Chinese translation unavailable: %s", exc)
        for raw_doi in req.dois:
            normalized = normalize_doi(raw_doi)
            if not normalized:
                results.append(
                    {
                        "doi": raw_doi,
                        "status": "error",
                        "message": "DOI 格式无效",
                    }
                )
                continue

            # Check if exists before upsert
            existing = get_paper_by_doi(session, normalized)
            action = "updated" if existing else "created"
            # Release the read transaction before waiting on CrossRef.
            session.rollback()

            # Fetch CrossRef metadata
            time.sleep(0.5)
            meta = fetch_metadata_by_doi(normalized, structured=True)
            if not meta:
                results.append(
                    {
                        "doi": normalized,
                        "status": "error",
                        "message": "CrossRef 未找到该 DOI 的元数据",
                    }
                )
                continue
            if "_error" in meta:
                logger.warning("CrossRef lookup failed for DOI %s: %s", normalized, meta["_error"])
                results.append(
                    {
                        "doi": normalized,
                        "status": "error",
                        "message": "CrossRef 查询暂时失败，请稍后重试",
                    }
                )
                continue

            raw = meta.get("raw") or {}
            paper_data = {
                "doi": normalized,
                "title": meta.get("title") or "(无标题)",
                "year": meta.get("year"),
                "journal_name": (
                    raw.get("container-title", [None])[0]
                    if raw.get("container-title")
                    else None
                ),
                "volume": str(raw.get("volume")) if raw.get("volume") is not None else None,
                "issue": str(raw.get("issue")) if raw.get("issue") is not None else None,
                "pages": str(raw.get("page")) if raw.get("page") is not None else None,
                "doi_meta": raw,
                "processing_status": "done",
                "review_outcome": "unreviewed",
                "source": "doi_import",
            }

            paper_data.update(
                _doi_import_entity_fields(
                    meta,
                    mark_as_target=mark_doi_import_as_target,
                )
            )

            # Abstract from CrossRef (if present)
            abstract = extract_crossref_abstract(meta)
            if abstract:
                paper_data["abstract"] = abstract

            paper = upsert_paper(session, paper_data)
            session.commit()

            # 作者标准化
            try:
                with session.begin_nested():
                    normalize_authors_for_paper(raw, paper.id, session)
                session.flush()
                session.expire(paper, ["authors"])
                refresh_metadata_complete(paper)
                session.commit()
            except Exception:
                logger.warning(
                    "作者标准化失败 paper_id=%s doi=%s",
                    paper.id,
                    normalized,
                    exc_info=True,
                )

            # Unified post-ingest completion: authoritative abstract fallback,
            # publication dates, then optional faithful Chinese translation.
            post_messages = []
            try:
                abstract_result = enrich_missing_abstract(
                    paper, refresh_crossref=False,
                )
                post_messages.append(f"摘要={abstract_result.source}")
                enrich_paper_dates(paper)
                if translation_client and paper.abstract and needs_chinese_abstract_package(paper):
                    translated = translate_abstract_to_chinese(
                        paper, translation_client, model=translation_model,
                    )
                    post_messages.append(f"中文={translated.source}")
                session.commit()
                session.expire(paper, ["authors"])
                refresh_metadata_complete(paper)
            except Exception as exc:
                session.rollback()
                logger.warning("DOI import post-enrichment failed for %s: %s", normalized, exc)

            # 自动补全期刊信息
            extra_msg = ""
            try:
                if matcher:
                    enrich_res = enrich_paper_journal(paper, session, matcher)
                    if enrich_res["status"] == "matched":
                        details = enrich_res.get("details", {})
                        if details:
                            extra_msg = f" [期刊: {', '.join(f'{k}={v}' for k, v in details.items())}]"
                        session.commit()
            except Exception:
                logger.warning('处理失败，已跳过该项（batch_doi_upload）', exc_info=True)
                pass

            results.append(
                {
                    "doi": normalized,
                    "status": action,
                    "paper_id": paper.id,
                    "title": paper.title,
                    "message": "成功" + extra_msg + (
                        f" [{', '.join(post_messages)}]" if post_messages else ""
                    ),
                }
            )
            session.commit()

        return {"results": results}

@router.post("/batch-pdf")
def batch_pdf_upload(
    files: List[UploadFile] = File(..., description="PDF files to import"),
    skip_title_check: bool = Query(False, description="Skip title similarity check"),
    replace_existing: bool = Query(False, description="Replace existing PDF for papers that already have one"),
    user: dict = Depends(require_uploader),
):
    """
    批量上传 PDF，自动提取 DOI、查询 CrossRef、创建/更新论文记录。

    三种场景：
      1. DOI 不存在 → 创建新 Paper 记录 + 归档 PDF
      2. DOI 存在但无 PDF → 直接归档 PDF 并链接到已有记录
      3. DOI 存在且有 PDF → 若 replace_existing=true 则覆盖，否则返回 existing_with_pdf

    每篇论文的处理是同步顺序执行的（含 ~0.5s CrossRef 延迟）。
    建议前端每次上传不超过 10 个 PDF，避免请求超时。
    """
    if len(files) > MAX_BATCH_PDF_FILES:
        raise HTTPException(status_code=413, detail="单次最多上传 10 个 PDF 文件")
    for upload in files:
        try:
            validate_upload_size(upload, PDF_MAX_UPLOAD_BYTES, label="PDF 文件")
        except UploadTooLarge as exc:
            raise HTTPException(
                status_code=413,
                detail=f"单个 PDF 文件不能超过 {format_upload_limit(PDF_MAX_UPLOAD_BYTES)}",
            ) from exc

    pdf_dir = storage.get_pdf_dir(settings.workspace)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = storage.get_temp_dir(settings.workspace)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    results: List[dict] = []

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        for file in files:
            filename = file.filename or "unknown.pdf"
            if not filename.lower().endswith(".pdf"):
                results.append({
                    "filename": filename,
                    "status": "error",
                    "doi": None,
                    "paper_id": None,
                    "internal_id": None,
                    "title": None,
                    "similarity": 0.0,
                    "message": "仅支持 PDF 文件",
                })
                continue

            tmp_path: Optional[Path] = None
            try:
                # 保存为临时文件
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, dir=tmp_dir) as tmp:
                    tmp_path = Path(tmp.name)
                    copy_upload(file, tmp, PDF_MAX_UPLOAD_BYTES)

                res = process_pdf_import(
                    pdf_path=tmp_path,
                    session=session,
                    next_id=None,
                    pdf_dir=pdf_dir,
                    skip_title_check=skip_title_check,
                    replace_existing=replace_existing,
                    workspace=settings.workspace,
                )

                # process_pdf_import 已返回最终状态，直接透传
                api_status = res.status

                results.append({
                    "filename": filename,
                    "status": api_status,
                    "doi": res.doi,
                    "paper_id": res.paper_id,
                    "internal_id": res.internal_id,
                    "title": res.title,
                    "similarity": round(res.similarity, 2),
                    "message": res.message,
                })
                # Persist each completed item independently and release the
                # connection before the next PDF's network/extraction work.
                session.commit()

            except UploadTooLarge as exc:
                raise HTTPException(
                    status_code=413,
                    detail=f"PDF 文件不能超过 {format_upload_limit(PDF_MAX_UPLOAD_BYTES)}",
                ) from exc
            except Exception:
                session.rollback()
                logger.exception("PDF batch import failed for %s", filename)
                results.append({
                    "filename": filename,
                    "status": "error",
                    "doi": None,
                    "paper_id": None,
                    "internal_id": None,
                    "title": None,
                    "similarity": 0.0,
                    "message": "处理失败，请稍后重试",
                })
            finally:
                if tmp_path and tmp_path.exists():
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        logger.warning('处理失败，已跳过该项（batch_pdf_upload）', exc_info=True)
                        pass

    return {"results": results}

@router.post("/{paper_id}/upload-pdf")
def upload_pdf(
    paper_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_uploader),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    try:
        validate_upload_size(file, PDF_MAX_UPLOAD_BYTES, label="PDF 文件")
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=f"PDF 文件不能超过 {format_upload_limit(PDF_MAX_UPLOAD_BYTES)}",
        ) from exc

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = ensure_paper_visible(session.get(Paper, paper_id), user)

        # Keep the current file until the replacement has been written and committed.
        old_path = None
        if paper.pdf_storage_path:
            old_path = storage.resolve_storage_path(paper.pdf_storage_path, settings.workspace)

        # Save PDF to the configured PDF storage root.
        pdf_dir = storage.get_pdf_dir(settings.workspace)
        pdf_dir.mkdir(parents=True, exist_ok=True)

        from paper_library.pdf_import import make_pdf_filename
        filename = make_pdf_filename(
            paper.id, paper.internal_id,
            paper.year, paper.title or "paper"
        )
        file_path = pdf_dir / filename

        # Avoid filename collision
        counter = 1
        original_path = file_path
        while file_path.exists():
            stem = original_path.stem
            file_path = pdf_dir / f"{stem}_{counter}.pdf"
            counter += 1

        try:
            copy_upload_to_path(file, file_path, PDF_MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise HTTPException(
                status_code=413,
                detail=f"PDF 文件不能超过 {format_upload_limit(PDF_MAX_UPLOAD_BYTES)}",
            ) from exc
        except Exception as exc:
            logger.exception("Failed to save uploaded PDF for paper %s", paper_id)
            raise HTTPException(status_code=500, detail="PDF 保存失败，请稍后重试") from exc

        ws = Path(settings.workspace).resolve()
        paper.pdf_storage_path = storage.ensure_relative_path(file_path, ws)
        refresh_pdf_flag(paper, settings.workspace)
        # Update timestamp to invalidate browser cache
        paper.updated_at = utc_now()
        try:
            session.commit()
        except Exception as exc:
            file_path.unlink(missing_ok=True)
            logger.exception("Failed to save uploaded PDF metadata for paper %s", paper_id)
            raise HTTPException(status_code=500, detail="PDF 保存失败，请稍后重试") from exc

        if old_path and old_path != file_path and old_path.exists():
            try:
                old_path.unlink()
            except Exception:
                logger.warning("Failed to remove replaced PDF for paper %s", paper_id, exc_info=True)

        # Extract text and images. Replacing the PDF starts a new processing
        # run, so a previous review decision must not survive it.
        try:
            start_processing(paper)
            img_dir = storage.get_paper_image_dir(paper.id, settings.workspace)
            img_dir.mkdir(parents=True, exist_ok=True)
            text, images = extract_text_and_images(
                str(file_path), img_dir, max_images=6
            )
            if text:
                paper.extracted_text = text
                paper.extracted_images = images or None
                paper.pdf_text_extracted = True
                complete_processing(paper)
                msg = "PDF 上传并解析成功"
            else:
                paper.pdf_text_extracted = False
                fail_processing(paper, "pdf_empty")
                msg = "PDF 上传成功，但文本提取为空"
            refresh_pdf_flag(paper, settings.workspace)
            session.expire(paper, ["authors"])
            refresh_metadata_complete(paper)
            session.commit()
            extracted = bool(text)

            # LLM enrichment from PDF full text
            logger.info(f"upload-pdf LLM check: extracted={extracted}, text_len={len(text) if text else 0}")
            if extracted and text:
                try:
                    from core.config_loader import ConfigLoader
                    cl = ConfigLoader(Path(settings.workspace) / settings.config_dir)
                    cfg = cl.load_all()
                    llm_cfg = cfg.get("runtime", {}).get("provider", {})
                    llm_client = OpenAICompatibleClient(
                        base_url=llm_cfg.get("base_url", ""),
                        api_key=llm_cfg.get("api_key", ""),
                        timeout_seconds=120,
                    )
                    import importlib.util
                    llm_script = Path(settings.workspace).resolve() / "scripts" / "llm_enrich_paper.py"
                    spec = importlib.util.spec_from_file_location("llm_enrich_paper", llm_script)
                    llm_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(llm_module)
                    enrich_paper = llm_module.enrich_paper
                    model_sequence = cfg.get("models", {}).get("routing", {}).get("default_auto_sequence", [])
                    selected_model = llm_cfg.get("model") or (model_sequence[0] if model_sequence else "")
                    if llm_client.available() and selected_model:
                        enrich_paper(paper.id, session, llm_client, source="both", model=selected_model)
                        session.commit()
                        msg += "，LLM 补全完成"
                except Exception as exc:
                    log_internal_error(logger, "PDF upload LLM enrichment", exc)
                    msg += "，LLM 补全暂时失败，请稍后重试"

        except Exception as exc:
            log_internal_error(logger, "PDF upload text extraction", exc)
            paper.pdf_text_extracted = False
            fail_processing(paper, "pdf_extract_failed")
            refresh_pdf_flag(paper, settings.workspace)
            session.commit()
            extracted = False
            msg = "PDF 上传成功，但文本解析失败，请稍后重试"

        return {
            "paper_id": paper.id,
            "pdf_path": str(file_path),
            "extracted": extracted,
            "message": msg,
        }

@router.post("/{paper_id}/download-pdf")
def download_pdf(
    paper_id: int,
    user: dict = Depends(require_reviewer),
):
    """根据 DOI 尝试开放获取来源和出版商官方 API 下载 PDF。"""
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = ensure_paper_visible(session.get(Paper, paper_id), user)

        if not paper.doi:
            raise HTTPException(status_code=400, detail="Paper 无 DOI，无法自动下载")

        # 检查是否已有 PDF
        if paper.pdf_storage_path:
            resolved = storage.resolve_storage_path(paper.pdf_storage_path, settings.workspace)
            if resolved and resolved.exists():
                refresh_pdf_flag(paper, settings.workspace)
                return {
                    "paper_id": paper.id,
                    "success": True,
                    "method": "existing",
                    "pdf_path": paper.pdf_storage_path,
                    "message": "PDF 已存在",
                }

        from core.service_config import runtime_config
        endpoints = runtime_config(Path(settings.workspace) / settings.config_dir).get("api_endpoints", {})
        downloader = PdfDownloader(
            elsevier_api_key=settings.elsevier_api_key or None,
            ieee_api_key=settings.ieee_api_key or None,
            semantic_scholar_api_key=settings.semantic_scholar_api_key or None,
            wiley_tdm_token=settings.wiley_tdm_token or None,
            unpaywall_email=settings.unpaywall_email or None,
            api_endpoints=endpoints,
            request_interval=1.0,
        )

        try:
            result = downloader.download_for_paper(paper, session, workspace=settings.workspace)
            session.commit()
        finally:
            downloader.close()

        if result.success:
            return {
                "paper_id": paper.id,
                "success": True,
                "method": result.method,
                "pdf_path": result.path,
                "message": f"通过 {result.method} 下载并导入成功",
            }
        else:
            record_download_error(paper)
            refresh_pdf_flag(paper, settings.workspace)
            session.commit()
            logger.warning("PDF download failed for paper %s: %s", paper.id, result.error)
            raise HTTPException(
                status_code=422,
                detail="PDF 下载失败，请检查服务配置后重试",
            )

@router.post("/{paper_id}/get-pdf")
def get_pdf(
    paper_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Prepare a PDF file and return a short-lived download URL."""
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = ensure_paper_visible(session.get(Paper, paper_id), user)
        if not paper.pdf_storage_path:
            raise HTTPException(status_code=404, detail="该论文无 PDF")

        pdf_path = storage.resolve_storage_path(paper.pdf_storage_path, settings.workspace)
        if not pdf_path or not pdf_path.exists():
            raise HTTPException(status_code=404, detail="PDF 文件不存在")

        from ...temp_download import build_temp_download_url, create_temp_file_from_path

        from ...governance_integration import GovernedPaperOutput
        quota = GovernedPaperOutput(user, [paper.id], action="paper.get_pdf", request=request)
        try:
            filename = f"{paper.internal_id or paper.id}.pdf"
            temp_info = create_temp_file_from_path(
                pdf_path,
                filename=filename,
                content_type="application/pdf",
            )
            quota.complete(temp_info, details={"format": "pdf"})
        except Exception as exc:
            quota.fail(exc)
            raise
        return {
            "success": True,
            "file_id": temp_info["file_id"],
            "filename": temp_info["filename"],
            "download_url": build_temp_download_url(temp_info),
            "expires_at": temp_info["expires_at"],
        }
