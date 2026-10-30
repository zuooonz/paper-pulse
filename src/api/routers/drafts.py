"""Draft HTTP endpoints. Business logic lives in ``api.services``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from paper_library.db import get_session

from ..config import settings
from ..dependencies import require_content_access, require_reviewer
from ..services import draft_images, draft_uploads, drafts as draft_service, publishing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/drafts", tags=["drafts"])


class PatchDraftRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    review_notes: Optional[str] = None
    meta: Optional[dict] = None
    selected_images: Optional[list[str]] = Field(default=None, max_length=100)


class PublishRequest(BaseModel):
    account_ids: list[int] = Field(default_factory=list, max_length=50)


class BatchPublishRequest(BaseModel):
    draft_ids: list[int] = Field(min_length=1, max_length=100)
    account_ids: list[int] = Field(default_factory=list, max_length=50)


class ScheduleRequest(BaseModel):
    account_ids: list[int] = Field(default_factory=list, max_length=50)
    scheduled_at: str


class BatchScheduleRequest(BaseModel):
    draft_ids: list[int] = Field(min_length=1, max_length=100)
    account_ids: list[int] = Field(default_factory=list, max_length=50)
    start_at: str
    interval_hours: float = 24.0
    jitter_hours: float = 2.0


def _http_error(exc: Exception, *, missing_draft_status: int = 404) -> HTTPException:
    if isinstance(exc, (publishing.InvalidPublishTargets, publishing.MissingDrafts)):
        status_code = 400 if not (isinstance(exc, publishing.MissingDrafts) and missing_draft_status == 404) else 404
        return HTTPException(status_code=status_code, detail=str(exc))
    if isinstance(exc, draft_service.DraftNotFoundError):
        return HTTPException(status_code=404, detail="Draft not found")
    if isinstance(exc, draft_service.InvalidDraftStatusTransition):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, publishing.PublishRecordNotFound):
        return HTTPException(status_code=404, detail="Record not found")
    if isinstance(exc, publishing.InvalidPublishTransition):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("")
def list_drafts(
    status: list[str] = Query(default=[]),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    user: dict = Depends(require_content_access),
):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        return draft_service.list_drafts(session, statuses=status, query_text=q, page=page, page_size=page_size, role=user.get("role", "admin"))


@router.get("/outbox")
def list_outbox(user: dict = Depends(require_content_access)):
    return publishing.list_outbox(workspace=settings.workspace, config_dir=settings.config_dir)


@router.delete("/outbox/clear")
def clear_outbox(user: dict = Depends(require_reviewer)):
    hidden = publishing.clear_terminal_publish_records(workspace=settings.workspace, config_dir=settings.config_dir)
    return {"ok": True, "hidden": hidden}


@router.delete("/outbox/{record_id}")
def cancel_outbox_record(record_id: int, user: dict = Depends(require_reviewer)):
    try:
        publishing.cancel_publish_record(record_id, workspace=settings.workspace, config_dir=settings.config_dir)
    except (publishing.PublishRecordNotFound, publishing.InvalidPublishTransition) as exc:
        raise _http_error(exc) from exc
    return {"ok": True}


@router.get("/{draft_id}")
def get_draft(draft_id: int, user: dict = Depends(require_content_access)):
    try:
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
            return draft_service.get_draft(session, draft_id, workspace=settings.workspace, role=user.get("role", "admin"))
    except draft_service.DraftNotFoundError as exc:
        raise _http_error(exc) from exc


@router.patch("/{draft_id}")
def patch_draft(draft_id: int, body: PatchDraftRequest, user: dict = Depends(require_reviewer)):
    try:
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
            return draft_service.patch_draft(
                session,
                draft_id,
                body.model_dump(exclude={"selected_images"}),
                reviewer=user["username"],
                selected_images=body.selected_images,
            )
    except (draft_service.DraftNotFoundError, draft_service.InvalidDraftStatusTransition) as exc:
        raise _http_error(exc) from exc


@router.delete("/{draft_id}")
def delete_draft(draft_id: int, user: dict = Depends(require_reviewer)):
    try:
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
            draft_service.delete_draft(session, draft_id, workspace=settings.workspace)
    except draft_service.DraftNotFoundError as exc:
        raise _http_error(exc) from exc
    return {"ok": True}


@router.post("/{draft_id}/publish")
def publish_draft(
    draft_id: int,
    body: PublishRequest,
    idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=255),
    user: dict = Depends(require_reviewer),
):
    try:
        tasks = publishing.enqueue_publish(
            [draft_id], body.account_ids, idempotency_key=idempotency_header,
            workspace=settings.workspace, config_dir=settings.config_dir,
        )
    except (publishing.InvalidPublishTargets, publishing.MissingDrafts) as exc:
        raise _http_error(exc, missing_draft_status=404) from exc
    return {"tasks": tasks}


@router.post("/batch-publish")
def batch_publish(
    body: BatchPublishRequest,
    idempotency_header: Optional[str] = Header(default=None, alias="Idempotency-Key", max_length=255),
    user: dict = Depends(require_reviewer),
):
    try:
        tasks = publishing.enqueue_publish(
            body.draft_ids, body.account_ids, idempotency_key=idempotency_header,
            workspace=settings.workspace, config_dir=settings.config_dir,
        )
    except (publishing.InvalidPublishTargets, publishing.MissingDrafts) as exc:
        raise _http_error(exc, missing_draft_status=400) from exc
    return {"tasks": tasks}


@router.post("/{draft_id}/schedule")
def schedule_draft(draft_id: int, body: ScheduleRequest, user: dict = Depends(require_reviewer)):
    try:
        scheduled_at = publishing.parse_scheduled_datetime(body.scheduled_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid scheduled_at ISO8601 format") from exc
    try:
        plan = publishing.schedule_records(
            [draft_id], body.account_ids, scheduled_at, workspace=settings.workspace,
            config_dir=settings.config_dir,
        )
    except (publishing.InvalidPublishTargets, publishing.MissingDrafts) as exc:
        raise _http_error(exc, missing_draft_status=404) from exc
    return {"ok": True, "draft_id": draft_id, "scheduled_at": plan["first_at"], "account_count": plan["account_count"]}


@router.post("/batch-schedule")
def batch_schedule(body: BatchScheduleRequest, user: dict = Depends(require_reviewer)):
    try:
        start_at = publishing.parse_scheduled_datetime(body.start_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid start_at ISO8601 format") from exc
    if body.interval_hours < 0:
        raise HTTPException(status_code=400, detail="interval_hours must be >= 0")
    if body.jitter_hours < 0:
        raise HTTPException(status_code=400, detail="jitter_hours must be >= 0")
    try:
        plan = publishing.schedule_records(
            body.draft_ids, body.account_ids, start_at, interval_hours=body.interval_hours,
            jitter_hours=body.jitter_hours, workspace=settings.workspace, config_dir=settings.config_dir,
        )
    except (publishing.InvalidPublishTargets, publishing.MissingDrafts) as exc:
        raise _http_error(exc, missing_draft_status=400) from exc
    return {"ok": True, **{key: plan[key] for key in ("draft_count", "account_count", "scheduled_records", "first_at", "last_at")}}


@router.get("/images/{image_path:path}")
def serve_image(
    image_path: str,
    thumbnail: bool = Query(default=False),
    size: int = Query(default=draft_images.IMAGE_THUMBNAIL_SIZE, ge=128, le=1024),
    user: dict = Depends(require_content_access),
):
    workspace = Path(settings.workspace).resolve()
    image_path_resolved, error = draft_images.resolve_image_request(image_path, workspace)
    if error:
        status, detail = {"invalid": (400, "Invalid path"), "outside": (400, "Path out of bounds"), "missing": (404, "Image not found")}[error]
        raise HTTPException(status_code=status, detail=detail)
    response_path = image_path_resolved
    if thumbnail:
        try:
            response_path = draft_images.get_image_thumbnail(image_path_resolved, workspace, size)
        except Exception:
            logger.warning("Thumbnail generation failed for %s", image_path_resolved, exc_info=True)
    return FileResponse(str(response_path), headers={
        "Cache-Control": f"private, max-age={draft_images.IMAGE_CACHE_MAX_AGE}",
        "Content-Disposition": f'inline; filename="{response_path.name}"',
    })


@router.post("/upload-word")
def upload_word(
    file: UploadFile = File(..., description="Word document (.docx)"),
    user: dict = Depends(require_reviewer),
):
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")
    try:
        return draft_uploads.upload_word(file, workspace=settings.workspace, config_dir=settings.config_dir)
    except draft_uploads.DraftUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
