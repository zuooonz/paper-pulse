"""Persistent, resumable background jobs for Word paper summaries."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from typing import Any, Iterable, Mapping

from fastapi import HTTPException

from paper_library.db import get_session
from paper_library.governance_models import SummaryJob
from paper_library.time_utils import ensure_utc, utc_now

from .summary_service import generate_summary_artifact
from .temp_download import get_temp_file

logger = logging.getLogger(__name__)


def _actor_snapshot(actor: Mapping[str, Any]) -> dict[str, str]:
    return {
        "actor_id": str(actor.get("actor_id") or ""),
        "username": str(actor.get("username") or ""),
        "role": str(actor.get("role") or ""),
        "name": str(actor.get("name") or actor.get("display_name") or ""),
        "display_name": str(actor.get("display_name") or actor.get("name") or ""),
        "department": str(actor.get("department") or ""),
        "login_source": str(actor.get("login_source") or "local"),
    }


def _fingerprint(actor_id: str, paper_ids: Iterable[int], profile: str, model: str) -> str:
    canonical = json.dumps({
        "actor_id": actor_id,
        "paper_ids": sorted(set(int(item) for item in paper_ids)),
        "profile": profile,
        "model": model,
        "pipeline": 4,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_is_available(row: SummaryJob, now: dt.datetime) -> bool:
    if row.status != "succeeded" or not row.result_json or not row.expires_at_utc:
        return False
    if ensure_utc(row.expires_at_utc) <= now:
        return False
    try:
        result = json.loads(row.result_json)
        return bool(result.get("file_id") and get_temp_file(str(result["file_id"])))
    except (TypeError, ValueError):
        return False


def serialize_summary_job(row: SummaryJob, *, reused: bool = False) -> dict[str, Any]:
    result = json.loads(row.result_json) if row.result_json else None
    error_detail: Any = row.error_message
    if row.error_message:
        try:
            error_detail = json.loads(row.error_message)
        except (TypeError, ValueError):
            pass
    return {
        "job_id": row.job_id,
        "status": row.status,
        "progress": row.progress,
        "stage": row.stage,
        "model": row.model,
        "paper_count": len(json.loads(row.paper_ids_json or "[]")),
        "attempt_count": row.attempt_count,
        "repair_count": row.repair_count,
        "result": result if row.status == "succeeded" else None,
        "error": {"code": row.error_code, "detail": error_detail} if row.status == "failed" else None,
        "reused": reused,
        "created_at": ensure_utc(row.created_at_utc).isoformat(),
        "updated_at": ensure_utc(row.updated_at_utc).isoformat(),
    }


def create_summary_job(
    paper_ids: Iterable[int],
    *,
    profile: str,
    model: str,
    actor: Mapping[str, Any],
) -> dict[str, Any]:
    ids = list(dict.fromkeys(int(item) for item in paper_ids))
    actor_data = _actor_snapshot(actor)
    fingerprint = _fingerprint(actor_data["actor_id"], ids, profile, model)
    now = utc_now()
    with get_session() as session:
        if session.bind and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        candidates = (
            session.query(SummaryJob)
            .filter(
                SummaryJob.actor_id == actor_data["actor_id"],
                SummaryJob.fingerprint == fingerprint,
                SummaryJob.status.in_(["queued", "running", "succeeded"]),
            )
            .order_by(SummaryJob.created_at_utc.desc())
            .limit(5)
            .all()
        )
        for candidate in candidates:
            if candidate.status in {"queued", "running"} or _result_is_available(candidate, now):
                payload = serialize_summary_job(candidate, reused=True)
                job_id = candidate.job_id
                break
        else:
            row = SummaryJob(
                job_id=uuid.uuid4().hex,
                fingerprint=fingerprint,
                actor_id=actor_data["actor_id"],
                actor_json=json.dumps(actor_data, ensure_ascii=False, separators=(",", ":")),
                paper_ids_json=json.dumps(ids, separators=(",", ":")),
                profile=profile,
                model=model,
                status="queued",
                progress=0,
                stage="等待处理",
            )
            session.add(row)
            session.flush()
            payload = serialize_summary_job(row)
            job_id = row.job_id
    queue_job_id = submit_summary_job(job_id) or job_id
    payload["job_id"] = queue_job_id
    return payload


def get_summary_job(job_id: str, actor: Mapping[str, Any]) -> dict[str, Any]:
    from worker.queue import find_by_dedupe_key, get_job

    queue_job = get_job(job_id)
    summary_id = job_id
    if queue_job and queue_job.get("kind") == "summary_job":
        summary_id = str((queue_job.get("payload") or {}).get("summary_job_id") or job_id)
    else:
        queue_job = None
    with get_session() as session:
        row = session.query(SummaryJob).filter_by(job_id=summary_id).first()
        if not row or (row.actor_id != actor.get("actor_id") and actor.get("role") != "admin"):
            raise HTTPException(status_code=404, detail="总结任务不存在")
        payload = serialize_summary_job(row)
    payload["job_id"] = job_id
    queued = queue_job or find_by_dedupe_key(f"summary-job:{summary_id}")
    if queued:
        payload["status"] = queued["status"]
        if queued["status"] in {"queued", "running"} and payload.get("error"):
            payload["error"] = None
            payload["stage"] = "等待重试" if queued["status"] == "queued" else payload["stage"]
        if queued["status"] == "failed" and payload.get("error") is None:
            payload["error"] = {"code": "JOB_FAILED", "detail": "总结任务未能完成，请重新提交"}
    return payload


def rerender_summary_job(job_id: str, actor: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild a Word artifact from persisted report JSON without another LLM call."""
    from core.word_report_generator import generate_word_report
    from paper_library.governance import record_audit
    from paper_library.time_utils import local_now
    from .temp_download import build_temp_download_url, create_temp_file
    from worker.queue import get_job

    queue_job = get_job(job_id)
    if queue_job and queue_job.get("kind") != "summary_job":
        queue_job = None
    summary_id = str((queue_job.get("payload") or {}).get("summary_job_id") or job_id) if queue_job else job_id

    with get_session() as session:
        row = session.query(SummaryJob).filter_by(job_id=summary_id).first()
        if not row or (row.actor_id != actor.get("actor_id") and actor.get("role") != "admin"):
            raise HTTPException(status_code=404, detail="总结任务不存在")
        if not row.report_json:
            raise HTTPException(status_code=409, detail="总结内容尚未生成，无法重新生成 Word")
        report = json.loads(row.report_json)
        word_bytes = generate_word_report(report)
        temp_info = create_temp_file(
            content=word_bytes,
            filename=f"论文总结_{local_now().strftime('%Y%m%d')}.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        result = {
            "success": True,
            "download_url": build_temp_download_url(temp_info),
            "filename": temp_info["filename"],
            "model": row.model,
            "file_id": temp_info["file_id"],
            "expires_at": temp_info["expires_at"],
        }
        row.status = "succeeded"
        row.progress = 100
        row.stage = "Word 已重新生成"
        row.result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        row.expires_at_utc = dt.datetime.fromtimestamp(int(temp_info["expires_at"]), tz=dt.UTC)
        row.updated_at_utc = utc_now()
        payload = serialize_summary_job(row)
        payload["job_id"] = job_id
    record_audit(
        actor,
        action="paper.summary_rerender",
        resource_type="summary_job",
        resource_id=job_id,
        resource_count=len(json.loads(row.paper_ids_json or "[]")),
        result="success",
    )
    from worker.queue import find_by_dedupe_key, mark_succeeded
    queue_job = queue_job or find_by_dedupe_key(f"summary-job:{summary_id}")
    if queue_job:
        mark_succeeded(queue_job["id"], result)
    return payload


def _update_job(job_id: str, **changes: Any) -> None:
    with get_session() as session:
        row = session.query(SummaryJob).filter_by(job_id=job_id).first()
        if not row:
            return
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_at_utc = utc_now()


def _claim_job(job_id: str) -> tuple[list[int], str, str, dict[str, Any]] | None:
    with get_session() as session:
        if session.bind and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.query(SummaryJob).filter_by(job_id=job_id).first()
        if not row or row.status not in {"queued", "running", "failed"}:
            return None
        row.status = "running"
        row.progress = max(5, int(row.progress or 0))
        row.stage = "正在启动总结任务"
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.error_code = None
        row.error_message = None
        row.updated_at_utc = utc_now()
        return (
            [int(item) for item in json.loads(row.paper_ids_json)],
            row.profile,
            row.model,
            json.loads(row.actor_json),
        )


def _run_summary_job(job_id: str) -> None:
    claimed = _claim_job(job_id)
    if not claimed:
        return
    paper_ids, profile, model, actor = claimed

    def update_progress(value: int, stage: str) -> None:
        changes: dict[str, Any] = {"progress": max(5, min(99, value)), "stage": stage}
        if stage == "正在修复报告结构":
            changes["repair_count"] = max(1, (value - 35) // 15)
        _update_job(job_id, **changes)

    try:
        result, report, repair_count = generate_summary_artifact(
            paper_ids,
            profile=profile,
            model=model,
            actor=actor,
            progress=update_progress,
        )
        expires_at = dt.datetime.fromtimestamp(int(result["expires_at"]), tz=dt.UTC)
        _update_job(
            job_id,
            status="succeeded",
            progress=100,
            stage="已完成",
            repair_count=repair_count,
            report_json=json.dumps(report, ensure_ascii=False, separators=(",", ":")),
            result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            expires_at_utc=expires_at,
        )
    except HTTPException as exc:
        detail = exc.detail
        code = detail.get("code") if isinstance(detail, dict) else "SUMMARY_REQUEST_FAILED"
        if exc.status_code >= 500:
            logger.error(
                "Summary job %s returned HTTP %s",
                job_id,
                exc.status_code,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            detail = "总结服务暂时不可用，请稍后重试"
        _update_job(
            job_id,
            status="failed",
            progress=100,
            stage="生成失败",
            error_code=str(code),
            error_message=json.dumps(detail, ensure_ascii=False) if isinstance(detail, (dict, list)) else str(detail)[:1000],
        )
    except Exception as exc:
        logger.exception("Summary job %s failed", job_id)
        _update_job(
            job_id,
            status="failed",
            progress=100,
            stage="生成失败",
            error_code=type(exc).__name__,
            error_message="总结任务失败，请稍后重试",
        )


def submit_summary_job(job_id: str) -> str:
    from worker.queue import enqueue

    return enqueue(
        "summary_job",
        {"summary_job_id": job_id},
        dedupe_key=f"summary-job:{job_id}",
        max_attempts=3,
    )


def run_summary_job(job_id: str) -> dict[str, Any]:
    _run_summary_job(job_id)
    with get_session() as session:
        row = session.query(SummaryJob).filter_by(job_id=job_id).first()
        if row is None:
            raise LookupError(f"Summary job {job_id} was not found")
        if row.status == "failed":
            raise RuntimeError(row.error_message or "Summary generation failed")
        return json.loads(row.result_json) if row.result_json else {"status": row.status}


def start_summary_job_worker() -> None:
    """Compatibility no-op; the worker queue recovers expired leases."""
    logger.info("Summary jobs are executed by database-backed workers")


def stop_summary_job_worker() -> None:
    """Compatibility no-op; worker lifecycle is managed by python -m worker."""
