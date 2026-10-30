"""Video library API — upload, manage, and publish videos to multiple platforms.

Reuses channel_accounts from the tweet publishing system for credentials.
"""
from __future__ import annotations
import logging

import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import or_

from ..config import settings
from ..dependencies import require_content_access, require_reviewer
from ..error_responses import log_internal_error
from paper_library.db import get_session
from paper_library.models import ChannelAccount, ChannelVideo, Paper, VideoPublishRecord
from paper_library.time_utils import ensure_utc, format_api_datetime, parse_api_datetime, utc_now
from tools.storage import ensure_relative_path, get_storage_root, resolve_storage_path
from tools.upload_limits import (
    COVER_MAX_UPLOAD_BYTES,
    UploadTooLarge,
    VIDEO_MAX_UPLOAD_BYTES,
    copy_upload_to_path,
    format_upload_limit,
    validate_upload_size,
)
from paper_library.paper_lifecycle import is_usable_for_generation
from core.domain_config import generation_requires_target_entity
from core.entity_detector import get_target_label

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["videos"])

# Video storage directory (under workspace data/)
VIDEO_DIR_NAME = "videos"
COVER_DIR_NAME = "video_covers"
_VIDEO_UPDATE_FIELDS = frozenset({"title", "description", "tags", "source_paper_id", "status"})
MAX_VIDEO_PUBLISH_TARGETS = 50
MAX_VIDEO_BATCH_ITEMS = 20


def _validate_id_count(values: list[int], maximum: int, label: str) -> None:
    if not values or len(values) > maximum:
        raise HTTPException(status_code=422, detail=f"{label}数量需为 1 至 {maximum}")
    if len(set(values)) != len(values):
        raise HTTPException(status_code=422, detail=f"{label}不能包含重复项")


def _video_generation_fallback(title: str, abstract: str, default_tags: str, exc: Exception) -> dict:
    log_internal_error(logger, "Video draft LLM generation", exc)
    return {
        "video_title": title[:15],
        "description": abstract[:100],
        "tags": default_tags,
        "script": f"[视频脚本生成暂时失败，请稍后重试]\\n\\n论文: {title}\\n摘要: {abstract[:500]}",
    }


def _video_dir() -> Path:
    return get_storage_root(settings.workspace) / VIDEO_DIR_NAME


def _cover_dir() -> Path:
    return get_storage_root(settings.workspace) / COVER_DIR_NAME


def _fmt_db_datetime(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return format_api_datetime(ensure_utc(value))


# ── Helpers ────────────────────────────────────────────────────────

def _ensure_video_visible(video, user: dict, session) -> None:
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if user.get("role") in {"admin", "reviewer"} or not video.source_paper_id:
        return
    paper = session.get(Paper, video.source_paper_id)
    if paper is not None and paper.review_outcome == "excluded":
        raise HTTPException(status_code=404, detail="Video not found")


def _get_video_dict(video_id: int, session) -> dict | None:
    video = session.get(ChannelVideo, video_id)
    if video is None:
        return None
    return {
        "id": video.id, "title": video.title, "description": video.description, "tags": video.tags,
        "file_path": video.file_path, "file_size": video.file_size, "duration": video.duration,
        "cover_path": video.cover_path, "width": video.width, "height": video.height,
        "status": video.status, "source_paper_id": video.source_paper_id,
        "script": video.script,
        "created_at": _fmt_db_datetime(video.created_at),
        "updated_at": _fmt_db_datetime(video.updated_at),
    }


def _get_publish_records(video_id: int, session) -> list[dict]:
    rows = session.query(VideoPublishRecord).filter(
        VideoPublishRecord.video_id == video_id
    ).order_by(VideoPublishRecord.created_at.desc()).all()
    from worker.queue import find_by_dedupe_key
    result = []
    for row in rows:
        job = find_by_dedupe_key(f"publish-video-record:{row.id}")
        status = row.status
        error = row.error
        if job and job["status"] == "failed" and status in {"pending", "uploading"}:
            status = "failed"
            error = error or "视频发布任务未能完成，请重新提交"
        result.append({
            "id": row.id, "account_id": row.account_id, "channel": row.channel,
            "account_name": row.account_name, "status": status,
            "job_id": job["id"] if job else None,
            "job_status": job["status"] if job else None,
            "publish_url": row.publish_url, "error": error,
            "scheduled_at": _fmt_db_datetime(row.scheduled_at),
            "published_at": _fmt_db_datetime(row.published_at),
            "created_at": _fmt_db_datetime(row.created_at),
        })
    return result


def execute_video_publish(payload: dict) -> dict:
    """Publish one video record using credentials loaded inside the worker."""
    from publisher.credential_store import decrypt_credential
    from publisher.video_publisher import publish_video as publish

    record_id = int(payload["record_id"])
    with get_session(settings.workspace, settings.config_dir) as session:
        record = session.get(VideoPublishRecord, record_id)
        if record is None:
            return {"status": "missing", "record_id": record_id}
        if record.status in {"success", "failed", "cancelled"}:
            return {
                "status": record.status,
                "error": record.error,
                "url": record.publish_url,
                "record_id": record_id,
            }
        video = session.get(ChannelVideo, record.video_id)
        if video is None:
            record.status = "failed"
            record.error = "Video not found"
            record.published_at = utc_now()
            return {"status": "failed", "error": record.error}
        video_data = {
            "file_path": video.file_path,
            "title": video.title,
            "description": video.description,
            "tags": video.tags,
            "cover_path": video.cover_path,
            "video_id": video.id,
        }
        account_id = record.account_id
        channel = record.channel
        account_name = record.account_name

    cookie = None
    with get_session(settings.workspace, settings.config_dir) as session:
        account = session.get(ChannelAccount, account_id) if account_id is not None else None
        if account is None:
            result = {"status": "failed", "error": "Account not found"}
        else:
            try:
                cookie = decrypt_credential(account.credential)
            except Exception:
                logger.exception("Could not decrypt video publisher credentials for record %s", record_id)
                result = {"status": "failed", "error": "渠道凭据不可用，请重新绑定账号"}
    if account is not None and cookie is not None:
        try:
            result = publish(
                video_data["file_path"],
                video_data["title"],
                video_data["description"],
                video_data["tags"],
                cookie,
                channel,
                account_name,
                cover_path=str(video_data["cover_path"]) if video_data["cover_path"] else None,
            )
        except Exception:
            logger.exception("Video publishing failed for record %s", record_id)
            result = {"status": "failed", "error": "视频发布失败，请检查渠道状态后重试"}

    with get_session(settings.workspace, settings.config_dir) as session:
        record = session.get(VideoPublishRecord, record_id)
        if record is None or record.status == "cancelled":
            return {"status": "cancelled" if record else "missing", "record_id": record_id}
        record.status = result.get("status", "failed")
        record.publish_url = result.get("url")
        record.error = result.get("error")
        record.published_at = utc_now()
        if record.status == "success":
            video = session.get(ChannelVideo, video_data["video_id"])
            if video is not None:
                video.status = "published"
                video.updated_at = utc_now()
        return {"record_id": record_id, **result}


# ── GET /api/videos ────────────────────────────────────────────────

@router.get("")
def list_videos(
    q: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
    user: dict = Depends(require_content_access),
):
    """List uploaded videos with optional search/filter."""
    with get_session(str(settings.workspace), settings.config_dir) as session:
        query = session.query(ChannelVideo)
        if q:
            pattern = f"%{q}%"
            query = query.filter(or_(
                ChannelVideo.title.like(pattern),
                ChannelVideo.description.like(pattern),
                ChannelVideo.tags.like(pattern),
            ))
        if status:
            query = query.filter(ChannelVideo.status == status)
        if user.get("role") not in {"admin", "reviewer"}:
            query = query.outerjoin(Paper, ChannelVideo.source_paper_id == Paper.id).filter(
                or_(ChannelVideo.source_paper_id.is_(None), Paper.review_outcome != "excluded")
            )
        count = query.count()
        rows = query.order_by(ChannelVideo.created_at.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size).all()
        items = [_get_video_dict(row.id, session) for row in rows]

        return {"items": items, "total": count, "page": page, "page_size": page_size}


# ── POST /api/videos ───────────────────────────────────────────────

@router.post("")
def upload_video(
    title: str = Form(...),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    source_paper_id: Optional[int] = Form(None),
    cover: Optional[UploadFile] = File(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_reviewer),
):
    """Upload a video file + metadata."""
    try:
        validate_upload_size(file, VIDEO_MAX_UPLOAD_BYTES, label="视频文件")
        if cover is not None:
            validate_upload_size(cover, COVER_MAX_UPLOAD_BYTES, label="封面文件")
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=(
                f"视频不能超过 {format_upload_limit(VIDEO_MAX_UPLOAD_BYTES)}，"
                f"封面不能超过 {format_upload_limit(COVER_MAX_UPLOAD_BYTES)}"
            ),
        ) from exc

    video_dir = _video_dir()
    video_dir.mkdir(parents=True, exist_ok=True)

    # Save video file
    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    video_id = uuid.uuid4().hex[:12]
    video_name = f"{video_id}{ext}"
    video_path = video_dir / video_name
    try:
        copy_upload_to_path(file, video_path, VIDEO_MAX_UPLOAD_BYTES)
    except UploadTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=f"视频文件不能超过 {format_upload_limit(VIDEO_MAX_UPLOAD_BYTES)}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to save uploaded video")
        raise HTTPException(status_code=500, detail="视频保存失败，请稍后重试") from exc
    file_size = video_path.stat().st_size
    # Store paths relative to workspace, preserving absolute paths outside it.
    rel_path = ensure_relative_path(video_path, settings.workspace)

    # Save cover if provided
    cover_path = None
    cover_rel = None
    if cover and cover.filename:
        cover_dir = _cover_dir()
        cover_dir.mkdir(parents=True, exist_ok=True)
        cover_name = f"{video_id}_cover{Path(cover.filename).suffix or '.jpg'}"
        cover_path = cover_dir / cover_name
        try:
            copy_upload_to_path(cover, cover_path, COVER_MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            video_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=f"封面文件不能超过 {format_upload_limit(COVER_MAX_UPLOAD_BYTES)}",
            ) from exc
        except Exception as exc:
            video_path.unlink(missing_ok=True)
            logger.exception("Failed to save uploaded video cover")
            raise HTTPException(status_code=500, detail="封面保存失败，请稍后重试") from exc
        cover_rel = ensure_relative_path(cover_path, settings.workspace)

    # Probe video metadata
    duration, width, height = 0, 0, 0
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video_path)],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            duration = float(data.get("format", {}).get("duration", 0))
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    width = stream.get("width", 0)
                    height = stream.get("height", 0)
                    break
    except Exception:
        logger.warning('处理失败，已跳过该项（upload_video）', exc_info=True)
        pass

    with get_session(settings.workspace, settings.config_dir) as session:
        session.add(ChannelVideo(
            title=title, description=description, tags=tags, file_path=rel_path,
            file_size=file_size, duration=duration, cover_path=cover_rel,
            width=width, height=height, status="draft", source_paper_id=source_paper_id,
            created_at=utc_now(), updated_at=utc_now(),
        ))
        session.commit()

    return {"status": "ok", "video_name": video_name, "file_size": file_size,
            "duration": duration, "width": width, "height": height}


# ── GET /api/videos/outbox ────────────────────────────────────────

@router.get("/outbox")
def get_video_outbox(user: dict = Depends(require_reviewer)):
    """Return recent publish records across videos for the mobile outbox."""
    with get_session(settings.workspace, settings.config_dir) as session:
        rows = session.query(VideoPublishRecord, ChannelVideo.title).join(
            ChannelVideo, ChannelVideo.id == VideoPublishRecord.video_id
        ).order_by(VideoPublishRecord.created_at.desc()).limit(300).all()
    return {"records": [{
        "id": record.id, "video_id": record.video_id,
        "video_title": title or f"Video #{record.video_id}",
        "account_id": record.account_id, "channel": record.channel,
        "account_name": record.account_name, "status": record.status,
        "publish_url": record.publish_url, "error": record.error,
        "scheduled_at": _fmt_db_datetime(record.scheduled_at),
        "published_at": _fmt_db_datetime(record.published_at),
        "created_at": _fmt_db_datetime(record.created_at),
    } for record, title in rows]}


# ── GET /api/videos/{id} ───────────────────────────────────────────

@router.get("/{video_id}")
def get_video(video_id: int, user: dict = Depends(require_content_access)):
    with get_session(settings.workspace, settings.config_dir) as session:
        row = session.get(ChannelVideo, video_id)
        _ensure_video_visible(row, user, session)
        video = _get_video_dict(video_id, session)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        video["publish_records"] = _get_publish_records(video_id, session)
        return video


# ── PATCH /api/videos/{id} ─────────────────────────────────────────

class VideoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    source_paper_id: Optional[int] = None
    status: Optional[str] = None

@router.patch("/{video_id}")
async def update_video(
    video_id: int,
    request: Request,
    user: dict = Depends(require_reviewer),
):
    data = await request.json()
    changes = {field: data[field] for field in _VIDEO_UPDATE_FIELDS if field in data and data[field] is not None}
    if not changes:
        return {"status": "ok", "note": "no fields"}
    with get_session(settings.workspace, settings.config_dir) as session:
        video = session.get(ChannelVideo, video_id)
        if video is None:
            return {"error": "not found"}
        for field, value in changes.items():
            assert field in _VIDEO_UPDATE_FIELDS, "video update column must be allowlisted"
            setattr(video, field, value)
        video.updated_at = utc_now()
        session.commit()
    return {"status": "ok", "updated": list(changes)}


# ── DELETE /api/videos/{id} ────────────────────────────────────────

@router.delete("/{video_id}")
def delete_video(video_id: int, user: dict = Depends(require_reviewer)):
    with get_session(settings.workspace, settings.config_dir) as session:
        video = session.get(ChannelVideo, video_id)
        if video is None:
            return {"error": "not found"}
        # Delete files
        for p in (video.file_path, video.cover_path):
            if p and Path(p).exists():
                Path(p).unlink(missing_ok=True)
        # Delete records + video
        session.query(VideoPublishRecord).filter(VideoPublishRecord.video_id == video_id).delete(synchronize_session=False)
        session.delete(video)
        session.commit()
    return {"status": "ok"}


# ── POST /api/videos/{id}/publish ──────────────────────────────────

@router.post("/{video_id}/publish")
def publish_video(
    video_id: int,
    account_ids: list[int] = Form(...),
    user: dict = Depends(require_reviewer),
):
    """Publish video to one or more channel accounts."""
    _validate_id_count(account_ids, MAX_VIDEO_PUBLISH_TARGETS, "发布账号")
    from worker.queue import enqueue

    with get_session(settings.workspace, settings.config_dir) as session:
        video = session.get(ChannelVideo, video_id)
        if video is None:
            return {"error": "Video not found"}
        video_path = resolve_storage_path(video.file_path, settings.workspace)
        if video_path is None or not video_path.exists():
            return {"error": "Video file missing on disk"}

        # Get account credentials
        accounts = session.query(ChannelAccount).filter(
            ChannelAccount.id.in_(account_ids)
        ).all()

        if not accounts:
            return {"error": "No valid accounts found"}

        results = []
        for acc in accounts:
            acc_id, channel, acc_name = acc.id, acc.channel, acc.account_name
            # Create publish record
            record = VideoPublishRecord(
                video_id=video_id, account_id=acc_id, channel=channel,
                account_name=acc_name, status="uploading", created_at=utc_now(),
            )
            session.add(record)
            session.flush()
            record_id = record.id
            job_id = enqueue(
                "publish_video",
                {"record_id": record_id},
                dedupe_key=f"publish-video-record:{record_id}",
                max_attempts=3,
                inline=False,
                session=session,
            )
            results.append({
                "account_id": acc_id,
                "channel": channel,
                "account_name": acc_name,
                "record_id": record_id,
                "job_id": job_id,
            })
        # Each video publish record and its durable job commit together.
    return {"status": "ok", "results": results}


# ── POST /api/videos/batch-publish ─────────────────────────────────

@router.post("/batch-publish")
def batch_publish(
    video_ids: list[int] = Form(...),
    account_ids: list[int] = Form(...),
    user: dict = Depends(require_reviewer),
):
    """Publish multiple videos to multiple accounts at once."""
    _validate_id_count(video_ids, MAX_VIDEO_BATCH_ITEMS, "视频")
    _validate_id_count(account_ids, MAX_VIDEO_PUBLISH_TARGETS, "发布账号")
    all_results = []
    for vid in video_ids:
        # Reuse single publish logic
        result = publish_video(vid, account_ids, user)
        all_results.append({"video_id": vid, **result})
    return {"results": all_results}


# ── POST /api/videos/generate ──────────────────────────────────────

class GenerateRequest(BaseModel):
    paper_id: int

@router.post("/generate")
def generate_video_script(req: GenerateRequest, user: dict = Depends(require_reviewer)):
    """
    Generate a video script + metadata from a paper using LLM.
    Creates a video draft entry with the generated content.
    """
    from core.domain_config import domain_tags, load_domain
    active_domain = load_domain(
        Path(settings.workspace) / settings.config_dir / "domain.yaml",
    )
    default_tags = ",".join(domain_tags(active_domain))

    with get_session(settings.workspace, settings.config_dir) as session:
        paper = session.query(Paper).filter(Paper.internal_id == req.paper_id).first()
        if not paper:
            return {"error": f"Paper {req.paper_id} not found"}
        if not is_usable_for_generation(
            paper,
            generation_requires_target_entity(active_domain),
            get_target_label(),
        ):
            raise HTTPException(status_code=400, detail="论文需完成处理、审核通过且符合生成课题范围后才能生成")

        # Build prompt for video script generation
        title = (paper.title_zh or paper.title or "")
        abstract = (paper.abstract or "")[:2000]
        journal = paper.journal_name or ""
        year = paper.year or ""

        prompt = f"""你是一个科研短视频脚本撰写专家。请根据以下论文信息，生成一个60秒短视频脚本。

当前领域：{active_domain.get('name', '科研领域')}。{(active_domain.get('prompt') or {}).get('domain_intro', '')}

论文标题: {title}
期刊: {journal} ({year})
摘要: {abstract}

请生成：
1. 视频标题（吸引眼球，15字以内）
2. 视频简介（100字以内）
3. 标签（5个，逗号分隔）
4. 60秒口播脚本（分段，每段标注时长）

以JSON格式输出：{{"video_title": "...", "description": "...", "tags": "...", "script": "..."}}"""

        # Call LLM
        try:
            from core.llm_client import OpenAICompatibleClient

            llm = OpenAICompatibleClient(settings.llm_base_url, settings.llm_api_key)
            if not llm.available():
                raise RuntimeError("未配置 LLM 服务地址或 API 密钥")
            response = llm.chat(
                settings.llm_model,
                [{"role": "user", "content": prompt}],
            )
            import re
            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*"video_title"[^{}]*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                # Fallback: use raw response
                result = {"video_title": title[:15], "description": abstract[:100], "tags": default_tags, "script": response}
        except Exception as exc:
            result = _video_generation_fallback(title, abstract, default_tags, exc)

        # Create video draft
        video = ChannelVideo(
            title=result.get("video_title", title[:15]),
            description=result.get("description", ""), tags=result.get("tags", ""),
            status="draft", source_paper_id=req.paper_id, script=result.get("script", ""),
            created_at=utc_now(), updated_at=utc_now(),
        )
        session.add(video)
        session.flush()
        vid = video.id
        session.commit()

        return {"status": "ok", "video_id": vid, **{k: result.get(k, "") for k in ["video_title", "description", "tags", "script"]}}


# ── POST /api/videos/{id}/schedule ─────────────────────────────────

class ScheduleRequest(BaseModel):
    scheduled_at: str  # ISO datetime
    account_ids: list[int] = Field(min_length=1, max_length=MAX_VIDEO_PUBLISH_TARGETS)

@router.post("/{video_id}/schedule")
def schedule_video(video_id: int, req: ScheduleRequest, user: dict = Depends(require_reviewer)):
    """Schedule video publishing for a future time."""
    with get_session(settings.workspace, settings.config_dir) as session:
        video = session.get(ChannelVideo, video_id)
        if video is None:
            return {"error": "Not found"}
        try:
            scheduled_at = parse_api_datetime(req.scheduled_at, naive_as_local=True)
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for aid in req.account_ids:
            account = session.get(ChannelAccount, aid)
            if account is None:
                continue
            session.add(VideoPublishRecord(
                video_id=video_id, account_id=account.id, channel=account.channel,
                account_name=account.account_name, status="scheduled", scheduled_at=scheduled_at,
                created_at=utc_now(),
            ))
        video.status = "approved"
        video.updated_at = utc_now()
        session.commit()
    return {"status": "ok", "scheduled_at": req.scheduled_at}


# ── GET /api/videos/file/{id} ──────────────────────────────────────

@router.get("/file/{video_id}")
def serve_video_file(video_id: int, user: dict = Depends(require_content_access)):
    """Serve video file for playback."""
    from fastapi.responses import FileResponse
    with get_session(settings.workspace, settings.config_dir) as session:
        video = session.get(ChannelVideo, video_id)
        _ensure_video_visible(video, user, session)
        file_path = video.file_path if video is not None else None
    if not file_path:
        raise HTTPException(status_code=404, detail="Video not found")
    path = resolve_storage_path(file_path, settings.workspace)
    if path is None or not path.exists():
        return {"error": f"File missing: {path}"}
    return FileResponse(str(path), media_type="video/mp4")


