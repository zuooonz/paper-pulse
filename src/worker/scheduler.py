"""Single-leader scheduler process; run with ``python -m worker.scheduler``."""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import socket
import threading
import time
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from paper_library.models import ChannelVideo, Job, PublishRecord, SchedulerLease, VideoPublishRecord
from paper_library.governance_models import SummaryJob
from .queue import _session, cleanup_terminal_jobs, enqueue, find_jobs, run_inline_job

logger = logging.getLogger("worker.scheduler")
LEASE_NAME = "main"
LEASE_SECONDS = 120
RENEW_SECONDS = 30
SCHEDULE_INTERVAL_SECONDS = 300
PUBLISH_PENDING_REQUEUE_MINUTES = 10
PUBLISH_STUCK_MINUTES = max(1, int(os.environ.get("PUBLISH_STUCK_MINUTES", "30")))


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def acquire_lease(owner: str, *, lease_seconds: int = LEASE_SECONDS) -> bool:
    now = _utcnow()
    with _session() as session:
        if session.bind and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        lease = session.execute(
            select(SchedulerLease).where(SchedulerLease.name == LEASE_NAME).with_for_update()
        ).scalar_one_or_none()
        if lease is None:
            session.add(SchedulerLease(
                name=LEASE_NAME,
                locked_by=owner,
                locked_until=now + dt.timedelta(seconds=lease_seconds),
                updated_at=now,
            ))
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                return False
            return True
        current_until = lease.locked_until
        if current_until.tzinfo is None:
            current_until = current_until.replace(tzinfo=dt.UTC)
        if lease.locked_by != owner and current_until > now:
            return False
        lease.locked_by = owner
        lease.locked_until = now + dt.timedelta(seconds=lease_seconds)
        lease.updated_at = now
        return True


def _queue_scheduled_publishes(workspace: str, config_dir: str) -> int:
    from api.scheduler import _should_execute
    from api.services.publishing import transition_publish_record
    from paper_library.db import get_session

    now = _utcnow()
    added = 0
    queued_ids = []
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = (
            session.query(PublishRecord)
            .filter(PublishRecord.status == "scheduled")
            .order_by(PublishRecord.scheduled_at)
            .all()
        )
        due_records = [record for record in records if _should_execute(record, now)]
        for record in due_records:
            if not transition_publish_record(session, record.id, "pending", now=now):
                continue
            job_id = enqueue(
                "publish_draft",
                {"record_id": record.id, "workspace": workspace, "config_dir": config_dir},
                dedupe_key=f"publish-draft-record:{record.id}",
                run_after=now,
                max_attempts=3,
                inline=False,
                session=session,
                requeue_terminal=True,
            )
            added += 1
            logger.info("Scheduled publish record %s queued as job %s", record.id, job_id)
            queued_ids.append(job_id)
    if os.environ.get("JOBS_INLINE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        for job_id in queued_ids:
            run_inline_job(job_id)
    return added


def _reconcile_pending_publishes(workspace: str, config_dir: str) -> int:
    """Recover old pending records and settle stuck publishing records."""
    from paper_library.db import get_session
    from api.services.publishing import transition_publish_record

    now = _utcnow()
    cutoff = now - dt.timedelta(minutes=PUBLISH_PENDING_REQUEUE_MINUTES)
    publishing_cutoff = now - dt.timedelta(minutes=PUBLISH_STUCK_MINUTES)
    requeued = 0
    reconciled = 0
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = (
            session.query(PublishRecord)
            .filter(PublishRecord.status == "pending", PublishRecord.created_at <= cutoff)
            .order_by(PublishRecord.created_at)
            .all()
        )
        for record in records:
            dedupe_key = f"publish-draft-record:{record.id}"
            existing = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
            if existing is not None and existing.status in {"queued", "running"}:
                continue
            job_id = enqueue(
                "publish_draft",
                {"record_id": record.id, "workspace": workspace, "config_dir": config_dir},
                dedupe_key=dedupe_key,
                run_after=now,
                max_attempts=3,
                inline=False,
                session=session,
                requeue_terminal=True,
            )
            requeued += 1
            logger.warning("Pending publish record %s recovered as job %s", record.id, job_id)

        stuck_records = (
            session.query(PublishRecord)
            .filter(PublishRecord.status == "publishing")
            .all()
        )
        for record in stuck_records:
            updated_at = record.updated_at or record.created_at
            if updated_at is None:
                continue
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=dt.UTC)
            if updated_at > publishing_cutoff:
                continue

            dedupe_key = f"publish-draft-record:{record.id}"
            job = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
            if job is None:
                if transition_publish_record(session, record.id, "failed", error="发布记录超时且对应任务不存在", now=now):
                    reconciled += 1
                    logger.error("Stuck publish record %s has no durable job", record.id)
                continue
            if job.status not in {"succeeded", "failed", "cancelled"}:
                continue

            result = job.result if isinstance(job.result, dict) else {}
            if job.status == "succeeded" and result.get("status") == "success":
                target_status = "success"
                error = None
            else:
                target_status = "cancelled" if job.status == "cancelled" else "failed"
                error = (
                    result.get("error")
                    or job.error
                    or ("发布任务已取消" if job.status == "cancelled" else "发布任务结束但未成功")
                )
            changed = transition_publish_record(
                session, record.id, target_status, url=result.get("url"), error=error, now=now
            )
            if changed:
                reconciled += 1
                logger.warning(
                    "Reconciled stuck publish record %s from terminal job %s (%s)",
                    record.id,
                    job.id,
                    job.status,
                )
    return requeued + reconciled


def _queue_scheduled_video_publishes(workspace: str, config_dir: str) -> int:
    """Atomically queue due video publishes with their record state change."""
    from paper_library.db import get_session

    now = _utcnow()
    queued = 0
    queued_ids = []
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = session.query(VideoPublishRecord).filter(
            VideoPublishRecord.status == "scheduled"
        ).order_by(VideoPublishRecord.scheduled_at).all()
        for record in records:
            scheduled_at = record.scheduled_at
            if scheduled_at is None:
                continue
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=dt.UTC)
            if scheduled_at > now:
                continue
            record.status = "uploading"
            job_id = enqueue(
                "publish_video",
                {"record_id": record.id},
                dedupe_key=f"publish-video-record:{record.id}",
                run_after=now,
                max_attempts=3,
                inline=False,
                session=session,
                requeue_terminal=True,
            )
            queued += 1
            logger.info("Scheduled video publish record %s queued as job %s", record.id, job_id)
            queued_ids.append(job_id)
    if os.environ.get("JOBS_INLINE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        for job_id in queued_ids:
            run_inline_job(job_id)
    return queued


def _reconcile_video_publishes(workspace: str, config_dir: str) -> int:
    """Settle video publish records left uploading by failed or missing jobs."""
    from paper_library.db import get_session

    now = _utcnow()
    cutoff = now - dt.timedelta(minutes=PUBLISH_STUCK_MINUTES)
    reconciled = 0
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = session.query(VideoPublishRecord).filter(
            VideoPublishRecord.status == "uploading",
            VideoPublishRecord.created_at <= cutoff,
        ).all()
        for record in records:
            job = session.query(Job).filter(
                Job.dedupe_key == f"publish-video-record:{record.id}"
            ).first()
            if job is None:
                record.status = "failed"
                record.error = "视频发布记录超时且对应任务不存在"
                record.published_at = now
                reconciled += 1
                continue
            if job.status not in {"succeeded", "failed", "cancelled"}:
                continue

            result = job.result if isinstance(job.result, dict) else {}
            if job.status == "succeeded" and result.get("status") == "success":
                record.status = "success"
                record.publish_url = result.get("url")
                record.error = None
                video = session.get(ChannelVideo, record.video_id)
                if video is not None:
                    video.status = "published"
                    video.updated_at = now
            else:
                record.status = "failed"
                record.error = "视频发布任务未能完成，请重新提交"
            record.published_at = now
            reconciled += 1
    return reconciled


def _reconcile_summary_jobs(workspace: str, config_dir: str) -> int:
    """Repair summary rows stranded by worker loss or a missing queue row."""
    from paper_library.db import get_session

    now = _utcnow()
    reconciled = 0
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        rows = session.query(SummaryJob).filter(
            SummaryJob.status.in_(["queued", "running"])
        ).all()
        for row in rows:
            dedupe_key = f"summary-job:{row.job_id}"
            job = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
            if job is None:
                enqueue(
                    "summary_job",
                    {"summary_job_id": row.job_id},
                    dedupe_key=dedupe_key,
                    max_attempts=3,
                    inline=False,
                    session=session,
                    requeue_terminal=True,
                )
                row.status = "queued"
                row.stage = "任务已恢复排队"
                row.updated_at_utc = now
                reconciled += 1
                continue
            if job.status not in {"succeeded", "failed", "cancelled"}:
                continue

            row.status = "failed"
            row.progress = 100
            row.stage = "生成失败"
            row.error_code = "JOB_CANCELLED" if job.status == "cancelled" else "JOB_FAILED"
            row.error_message = "总结任务未能完成，请重新提交"
            row.updated_at_utc = now
            reconciled += 1
    return reconciled


def _queue_cookie_validation(workspace: str, config_dir: str) -> None:
    jobs = find_jobs(kind="cookie_validation", limit=200)
    latest = jobs[0] if jobs else None
    now = _utcnow()
    if latest:
        created = latest["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.UTC)
        due = created + dt.timedelta(days=21)
        if latest["status"] in {"queued", "running"} or due > now:
            return
        run_after = now
        dedupe = f"cookie-validation:{now.date().isoformat()}"
    else:
        # The historical scheduler did not run validation immediately on startup.
        run_after = now + dt.timedelta(days=21)
        dedupe = "cookie-validation:first"
    enqueue(
        "cookie_validation",
        {"workspace": workspace, "config_dir": config_dir},
        dedupe_key=dedupe,
        run_after=run_after,
        max_attempts=2,
    )


def _queue_pdf_flag_reconciliation(workspace: str, config_dir: str) -> None:
    try:
        interval_hours = float(os.environ.get("PDF_FLAG_RECONCILE_HOURS", "24"))
    except ValueError:
        logger.warning("Invalid PDF_FLAG_RECONCILE_HOURS; using 24 hours")
        interval_hours = 24
    if interval_hours <= 0:
        return

    jobs = find_jobs(kind="reconcile_pdf_flags", limit=200)
    latest = jobs[0] if jobs else None
    now = _utcnow()
    if latest:
        if latest["status"] in {"queued", "running"}:
            return
        created = latest["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.UTC)
        if created + dt.timedelta(hours=interval_hours) > now:
            return

    enqueue(
        "reconcile_pdf_flags",
        {"workspace": workspace, "config_dir": config_dir},
        dedupe_key=f"pdf-flag-reconcile:{uuid.uuid4().hex}",
        run_after=now,
        max_attempts=2,
    )


def scheduler_tick(
    workspace: str,
    config_dir: str,
    *,
    leadership_lost: threading.Event | None = None,
) -> None:
    from paper_library.governance import expire_access_requests, release_stale_reservations
    from api.scheduler import reset_stale_paper_states

    def leader_is_active() -> bool:
        return leadership_lost is None or not leadership_lost.is_set()

    if not leader_is_active():
        return
    released = release_stale_reservations()
    if released:
        logger.warning("Released %d stale quota reservation(s)", released)
    if not leader_is_active():
        return
    expired = expire_access_requests()
    if expired:
        logger.info("Marked %d access request(s) expired", expired)
    if not leader_is_active():
        return
    reset_stale_paper_states(workspace, config_dir)
    if not leader_is_active():
        return
    _queue_scheduled_publishes(workspace, config_dir)
    if not leader_is_active():
        return
    _reconcile_pending_publishes(workspace, config_dir)
    if not leader_is_active():
        return
    _queue_scheduled_video_publishes(workspace, config_dir)
    if not leader_is_active():
        return
    _reconcile_video_publishes(workspace, config_dir)
    if not leader_is_active():
        return
    _reconcile_summary_jobs(workspace, config_dir)
    if not leader_is_active():
        return
    _queue_cookie_validation(workspace, config_dir)
    if not leader_is_active():
        return
    _queue_pdf_flag_reconciliation(workspace, config_dir)
    if not leader_is_active():
        return
    try:
        retention_days = max(0, int(os.environ.get("JOB_RETENTION_DAYS", "30")))
    except ValueError:
        retention_days = 30
    removed = cleanup_terminal_jobs(older_than_days=retention_days)
    if removed:
        logger.info("Removed %d expired terminal jobs", removed)
    if leader_is_active() and os.environ.get("JOBS_INLINE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        for kind in ("cookie_validation", "reconcile_pdf_flags"):
            for job in find_jobs(kind=kind, statuses=("queued",), limit=100):
                run_after = job["run_after"]
                if run_after.tzinfo is None:
                    run_after = run_after.replace(tzinfo=dt.UTC)
                if run_after <= _utcnow():
                    run_inline_job(job["id"])


def _renew_lease_until(
    owner: str,
    stop: threading.Event,
    leadership_lost: threading.Event | None = None,
) -> None:
    while not stop.wait(RENEW_SECONDS):
        try:
            if not acquire_lease(owner):
                logger.error("Scheduler lease was lost while a tick was running")
                if leadership_lost is not None:
                    leadership_lost.set()
                return
        except Exception:
            logger.exception("Could not renew scheduler lease")
            if leadership_lost is not None:
                leadership_lost.set()
            return


def run_scheduler(stop_event: threading.Event | None = None) -> None:
    from api.config import settings

    stop = stop_event or threading.Event()
    owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    next_tick = 0.0
    logger.info("Scheduler started")
    while not stop.is_set():
        try:
            if acquire_lease(owner):
                now = time.monotonic()
                if now >= next_tick:
                    renewal_stop = threading.Event()
                    leadership_lost = threading.Event()
                    renewal = threading.Thread(
                        target=_renew_lease_until,
                        args=(owner, renewal_stop, leadership_lost),
                        daemon=True,
                        name="scheduler-lease-heartbeat",
                    )
                    renewal.start()
                    try:
                        scheduler_tick(
                            settings.workspace,
                            settings.config_dir,
                            leadership_lost=leadership_lost,
                        )
                    finally:
                        renewal_stop.set()
                        renewal.join(timeout=2)
                    next_tick = time.monotonic() + SCHEDULE_INTERVAL_SECONDS
            else:
                logger.debug("Another scheduler holds the lease")
        except Exception:
            logger.exception("Scheduler iteration failed")
        stop.wait(RENEW_SECONDS)
    logger.info("Scheduler stopped")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    stop = threading.Event()

    def request_stop(signum, _frame):
        logger.info("Received signal %s; stopping scheduler", signum)
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)
    from api.config import settings
    from paper_library.db import init_db

    init_db(workspace=settings.workspace, config_dir=settings.config_dir)
    run_scheduler(stop)


if __name__ == "__main__":
    main()
