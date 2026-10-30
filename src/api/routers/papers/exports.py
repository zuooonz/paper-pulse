"""Paper exports API routes."""
import logging

from fastapi import APIRouter

from fastapi import Depends
from fastapi import HTTPException
from typing import Optional
from paper_library.models import Paper
from fastapi import Query
from fastapi import Request
from sqlalchemy import case
from core.taxonomy_config import dimension_aliases
from core.taxonomy_config import dimension_items
from ...dependencies import get_current_user
from paper_library.db import get_session
import io
from paper_library.tag_enrichment import normalize_catalog_values
from paper_library.tag_enrichment import normalize_paradigm_selection
from paper_library.tag_enrichment import normalize_paradigms
import re
from ...config import settings
from ...error_responses import internal_http_error
from tools import storage

from .common import (
    _apply_sort,
    _build_base_query,
    _current_taxonomy,
    _get_ordered_authors,
    _impact_factor_subquery,
    _parse_ids,
    can_view_paper,
    ensure_paper_visible,
)

from .detail import _build_apa_citation

from .schemas import (
    BatchDownloadBody,
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/export")
def export_papers(
    request: Request,
    q: Optional[str] = Query(default=None),
    journal: Optional[str] = Query(default=None),
    year_from: Optional[int] = Query(default=None),
    year_to: Optional[int] = Query(default=None),
    paper_ids: Optional[str] = Query(default=None, description="Comma-separated paper IDs, e.g. 191,494,599"),
    entity_label: Optional[str] = Query(default=None, description="Filter by configured entity label"),
    affiliation: Optional[str] = Query(default=None, description="Search by author affiliation"),
    research_field: Optional[str] = Query(default=None, description="Search by a configured research topic"),
    research_topic: Optional[str] = Query(default=None, description="Structured research topic"),
    neuroscience_subtopic: Optional[str] = Query(default=None, description="Neuroscience subtopic"),
    country: Optional[str] = Query(default=None, description="Corresponding-author country"),
    province: Optional[str] = Query(default=None, description="Corresponding-author province"),
    city: Optional[str] = Query(default=None, description="Corresponding-author city"),
    institution_type: Optional[str] = Query(default=None, description="临床/科研"),
    department: Optional[str] = Query(default=None, description="Corresponding-author department/school"),
    processing_status: Optional[str] = Query(default=None, description="处理状态，多选逗号分隔"),
    review_outcome: Optional[str] = Query(default=None, description="审核结论，多选逗号分隔"),
    has_pdf: Optional[bool] = Query(default=None),
    metadata_complete: Optional[bool] = Query(default=None),
    tag: Optional[str] = Query(default=None, description="Filter by tag, e.g. '脑卒中'"),
    subject_disease: Optional[str] = Query(default=None),
    age_min: Optional[float] = Query(default=None, ge=0, le=120),
    age_max: Optional[float] = Query(default=None, ge=0, le=120),
    study_design: Optional[str] = Query(default=None),
    paradigm: Optional[str] = Query(default=None),
    has_multimodal: Optional[bool] = Query(default=None),
    multimodal_technique: Optional[str] = Query(default=None),
    multimodal_timing: Optional[str] = Query(default=None),
    sort_by: Optional[str] = Query(default=None),
    sort_order: str = Query(default="desc"),
    user: dict = Depends(get_current_user),
):
    ids = _parse_ids(paper_ids)
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        query = _build_base_query(
            session, q, journal, year_from, year_to, paper_ids=ids, entity_label=entity_label,
            affiliation=affiliation, research_field=research_field,
            research_topic=research_topic,
            neuroscience_subtopic=neuroscience_subtopic,
            processing_status=processing_status, review_outcome=review_outcome,
            has_pdf=has_pdf, metadata_complete=metadata_complete,
            tag=tag, country=country,
            province=province, city=city, institution_type=institution_type,
            department=department,
            subject_disease=subject_disease, age_min=age_min, age_max=age_max,
            study_design=study_design, paradigm=paradigm,
            has_multimodal=has_multimodal, multimodal_technique=multimodal_technique,
            multimodal_timing=multimodal_timing,
            restricted_role=(user.get("role") == "limited"),
            can_view_excluded=(user.get("role") in {"admin", "reviewer"}),
        )

        if_subq = _impact_factor_subquery(session)
        items_query = query.outerjoin(if_subq, Paper.journal_id == if_subq.c.journal_id)
        items_query = items_query.add_columns(if_subq.c.impact_factor, if_subq.c.jcr_quartile, if_subq.c.cas_quartile, if_subq.c.emerging_quartile)

        # When paper_ids is provided, preserve the order of selected papers instead of applying sort
        if ids:
            # Build a CASE expression to preserve the order of paper_ids
            case_expr = case(
                *[
                    (Paper.id == pid, idx)
                    for idx, pid in enumerate(ids)
                ],
                else_=len(ids)
            )
            items_query = items_query.order_by(case_expr)
        else:
            items_query = _apply_sort(items_query, sort_by, sort_order, if_subq)

        items = items_query.all()

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        # Regex matching control chars illegal in Excel (except \t, \n, \r)
        _ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

        def _sanitize(v):
            """Remove control characters that are illegal in Excel cells."""
            if isinstance(v, str):
                return _ILLEGAL_XML_RE.sub("", v)
            return v

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "论文合集"

        # 根据用户角色确定导出的列
        is_quota_limited = user.get("role") == "limited"
        export_taxonomy = _current_taxonomy()
        dimensions = export_taxonomy.get("dimensions") or {}
        dimension_labels = {
            key: value.get("label", key)
            for key, value in dimensions.items()
            if isinstance(value, dict)
        }

        if is_quota_limited:
            # 受限角色版本：仅导出必要的期刊与指标字段
            headers = [
                "论文编号", "英文标题", "中文标题", "期刊名称",
                "发表年份", "IF", "JCR分区", "课题",
            ]
        else:
            # 完整版本：包含所有信息
            headers = [
                "论文编号", "英文标题", "中文标题", "DOI", "期刊名称",
                "IF", "JCR分区", "中科院分区", "新锐分区",
                dimension_labels.get("research_topics", "研究主题"),
                dimension_labels.get("neuroscience_subtopics", "补充分类"),
                dimension_labels.get("diseases", "疾病与研究对象"),
                dimension_labels.get("study_design", "研究设计"),
                dimension_labels.get("paradigms", "研究范式"),
                "是否联用", dimension_labels.get("multimodal", "多模态技术"), "联用方式", "通讯作者单位地区",
                "通讯作者", "工作单位", "通讯作者.1", "工作单位.1",
                "通讯作者.2", "工作单位.2", "通讯作者.3", "工作单位.3",
                "第一作者", "工作单位.4", "共同第一作者", "工作单位.5",
                "共同第一作者.1", "工作单位.6",
                "使用设备", "通道数", "设备引用信息", "引用错误类型",
                "是否使用目标软件", "发表时间", "备注",
                "原始研究领域", "原始范式字段",
            ]

        # Header style: dark blue background, white bold font
        header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
        header_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
        header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin", color="D0D5DD"),
            right=Side(style="thin", color="D0D5DD"),
            top=Side(style="thin", color="D0D5DD"),
            bottom=Side(style="thin", color="D0D5DD"),
        )

        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_align
            cell.border = thin_border

        # Data style
        data_font = Font(name="微软雅黑", size=9)
        data_align = Alignment(vertical="center", wrap_text=False)
        timing_labels = {
            str(item.get("value") or item.get("key")): str(item.get("label"))
            for item in dimension_items(export_taxonomy, "multimodal_timing")
            if item.get("key") and item.get("label")
        }
        topic_aliases = dimension_aliases(export_taxonomy, "research_topics")

        for row_idx, (p, if_val, jcr_q, cas_q, em_q) in enumerate(items, start=2):
            # 作者数据
            ordered_authors = _get_ordered_authors(session, p.id) if session else []
            first_authors_list = [a for a in ordered_authors if a.get("is_first")]
            corr_authors_list = [a for a in ordered_authors if a.get("is_corresponding")]

            def _author_name(a, i):
                if i < len(a):
                    author = a[i]
                    return author.get("name_zh") or author.get("name", "")
                return ""

            def _author_aff(a, i):
                if i < len(a):
                    return a[i].get("affiliation", "")
                return ""

            fa = first_authors_list
            ca = corr_authors_list

            # 设备
            device = ""
            if p.equipment_brand and isinstance(p.equipment_brand, list):
                device = ", ".join(str(x) for x in p.equipment_brand if x)
            elif p.equipment_brand:
                device = str(p.equipment_brand)

            # 目标软件使用情况
            target_software = ""
            if p.uses_target_software is True:
                target_software = "是"
            elif p.uses_target_software is False:
                target_software = "否"

            # 接收时间
            accepted = p.published_date.isoformat() if p.published_date else ""

            def _join_classification(value) -> str:
                if value in (None, ""):
                    return ""
                if isinstance(value, list):
                    return "；".join(str(item).strip() for item in value if item not in (None, "") and str(item).strip())
                return str(value).strip()

            research_topics = p.research_topics
            if not isinstance(research_topics, list):
                research_topics = normalize_catalog_values(
                    p.research_fields,
                    topic_aliases,
                )
            paradigm_values = p.paradigms
            if not isinstance(paradigm_values, list):
                paradigm_values = normalize_paradigms(p.paradigm)
            disease_values = p.subject_diseases or ([p.subject_disease] if p.subject_disease else [])
            multimodal_state = "是" if p.uses_multimodal is True else "否" if p.uses_multimodal is False else ""
            relation_values = []
            for relation in p.multimodal_relations or []:
                if not isinstance(relation, dict) or not relation.get("method"):
                    continue
                timing = timing_labels.get(relation.get("timing"), relation.get("timing") or "未判断")
                relation_values.append(f"{relation['method']}：{timing}")

            structured_values = [
                _join_classification(research_topics),
                _join_classification(p.neuroscience_subtopics),
                _join_classification(disease_values),
                p.study_design or "",
                _join_classification(normalize_paradigm_selection(paradigm_values)),
                multimodal_state,
                _join_classification(p.multimodal_methods),
                "；".join(relation_values),
                p.corresponding_region or "",
            ]

            if is_quota_limited:
                # 受限角色版本：仅导出必要的期刊与指标字段
                values = [
                    p.internal_id or "",
                    p.title or "",
                    p.title_zh or "",
                    p.journal_name or "",
                    p.year or "",
                    round(if_val, 3) if if_val is not None else "",
                    jcr_q or "",
                    p.entity_label or "未标注",
                ]
            else:
                # 完整版本：包含所有信息
                values = [
                    p.internal_id or "",
                    p.title or "",
                    p.title_zh or "",
                    p.doi or "",
                    p.journal_name or "",
                    round(if_val, 3) if if_val is not None else "",
                    jcr_q or "",
                    cas_q or "",
                    em_q or "",
                    *structured_values,
                    _author_name(ca, 0), _author_aff(ca, 0),
                    _author_name(ca, 1), _author_aff(ca, 1),
                    _author_name(ca, 2), _author_aff(ca, 2),
                    _author_name(ca, 3), _author_aff(ca, 3),
                    _author_name(fa, 0), _author_aff(fa, 0),
                    _author_name(fa, 1), _author_aff(fa, 1),
                    _author_name(fa, 2), _author_aff(fa, 2),
                    device,
                    p.channel_count or "",
                    p.device_citation_text or "",
                    p.citation_error_type or "",
                    target_software,
                    accepted,
                    p.notes or "",
                    _join_classification(p.research_fields),
                    _join_classification(p.paradigm),
                ]
            for col_idx, val in enumerate(values, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=_sanitize(val))
                cell.font = data_font
                cell.alignment = data_align
                cell.border = thin_border

        # Auto-fit column widths
        for col_idx in range(1, len(headers) + 1):
            max_width = len(str(ws.cell(row=1, column=col_idx).value or "")) * 2
            for row_idx in range(2, min(len(items) + 2, 52)):  # sample first 50 rows
                val = ws.cell(row=row_idx, column=col_idx).value
                if val:
                    max_width = max(max_width, len(str(val)))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_width + 4, 40)

        # Freeze header row
        ws.freeze_panes = "A2"

        from ...governance_integration import GovernedPaperOutput
        quota = GovernedPaperOutput(
            user,
            [p.id for p, *_metrics in items],
            action="paper.export_excel",
            request=request,
        )
        try:
            output = io.BytesIO()
            wb.save(output)
            output.seek(0)

            # Save to temp storage and get download info
            from ...temp_download import build_temp_download_url, create_temp_file
            temp_info = create_temp_file(
                content=output.getvalue(),
                filename="papers.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            quota.complete(temp_info, details={"format": "xlsx"})
        except Exception as exc:
            quota.fail(exc)
            raise

        # Return temp URL for native browser download
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={
                "success": True,
                "file_id": temp_info["file_id"],
                "filename": temp_info["filename"],
                "download_url": build_temp_download_url(temp_info),
                "expires_at": temp_info["expires_at"],
            },
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

@router.post("/batch-download-pdf")
def batch_download_pdf(
    body: BatchDownloadBody,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Download PDFs for selected papers as a zip archive.

    Two-step download approach for native browser progress:
    1. Create zip file and save to temp storage
    2. Return temp_url for browser to download with native progress
    """
    import zipfile
    import tempfile
    from ...temp_download import build_temp_download_url, create_temp_file

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        papers = [
            paper for paper in session.query(Paper).filter(Paper.id.in_(body.paper_ids)).all()
            if can_view_paper(paper, user)
        ]

        if not papers:
            raise HTTPException(status_code=404, detail="No papers found")

        # Collect valid PDF paths first
        pdf_files = []
        for paper in papers:
            if not paper.pdf_storage_path:
                continue
            pdf_path = storage.resolve_storage_path(paper.pdf_storage_path, settings.workspace)
            if pdf_path and pdf_path.exists():
                pdf_files.append((paper.id, pdf_path, pdf_path.name))

        if not pdf_files:
            raise HTTPException(status_code=404, detail="所选论文均无 PDF")

        from ...governance_integration import GovernedPaperOutput
        quota = GovernedPaperOutput(
            user,
            [paper_id for paper_id, _path, _name in pdf_files],
            action="paper.batch_pdf",
            request=request,
        )

        # Create temp file for zip
        temp_root = storage.get_temp_dir(settings.workspace)
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=temp_root)
        temp_zip_path = temp_zip.name
        temp_zip.close()

        try:
            # Write zip to temp file (no compression, low CPU)
            with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
                for _paper_id, pdf_path, arcname in pdf_files:
                    try:
                        zf.write(str(pdf_path), arcname)
                    except Exception:
                        logger.warning('处理失败，已跳过该项（batch_download_pdf）', exc_info=True)
                        continue  # Skip files that can't be read

            # Read the zip file and save to temp storage
            with open(temp_zip_path, "rb") as f:
                zip_content = f.read()

            # Save to temp storage and get download info
            temp_info = create_temp_file(
                content=zip_content,
                filename=f"papers_{len(pdf_files)}.zip",
                content_type="application/zip",
            )
            quota.complete(temp_info, details={"format": "zip", "pdf_count": len(pdf_files)})

            # Clean up the temp zip file
            try:
                import os
                os.unlink(temp_zip_path)
            except Exception:
                logger.debug('可选步骤失败，继续执行（batch_download_pdf）', exc_info=True)
                pass

            # Return temp URL for native browser download
            return {
                "success": True,
                "file_id": temp_info["file_id"],
                "filename": temp_info["filename"],
                "download_url": build_temp_download_url(temp_info),
                "expires_at": temp_info["expires_at"],
            }
        except Exception as e:
            quota.fail(e)
            # Clean up temp file on error
            try:
                import os
                os.unlink(temp_zip_path)
            except Exception:
                logger.debug('可选步骤失败，继续执行（batch_download_pdf）', exc_info=True)
                pass
            raise internal_http_error(
                logger,
                "Batch PDF archive creation",
                e,
                detail="PDF 打包失败，请稍后重试",
            ) from e

@router.get("/{paper_id}/citation")
def get_citation(
    paper_id: int,
    user: dict = Depends(get_current_user),
):
    """Return a complete APA 7th edition citation using doi_meta author data."""
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = ensure_paper_visible(session.get(Paper, paper_id), user)

        citation = _build_apa_citation(paper, session)
        return {"paper_id": paper_id, "citation": citation}
