"""Database-backed job queue with PostgreSQL and SQLite claim strategies."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Iterable

from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paper_library.db import get_session
from paper_library.models import Job

logger = logging.getLogger(__name__)


def _session():
    from api.config import settings

    return get_session(settings.workspace, settings.config_dir)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "payload": job.payload,
        "status": job.status,
        "priority": job.priority,
        "run_after": job.run_after,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "locked_by": job.locked_by,
        "locked_at": job.locked_at,
        "heartbeat_at": job.heartbeat_at,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "dedupe_key": job.dedupe_key,
    }


def enqueue(
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    run_after: dt.datetime | None = None,
    max_attempts: int = 3,
    priority: int = 0,
    inline: bool | None = None,
    session: Session | None = None,
    requeue_terminal: bool = False,
) -> str:
    """Persist work and return its ID.

    Supplying ``session`` makes the caller responsible for committing the job
    together with related records. Inline execution only occurs when this
    function owns the transaction.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")
    job_id = uuid.uuid4().hex
    now = _now()
    scheduled_at = run_after or now
    if scheduled_at.tzinfo is None or scheduled_at.tzinfo.utcoffset(scheduled_at) is None:
        scheduled_at = scheduled_at.replace(tzinfo=dt.UTC)
    def persist(target_session: Session) -> tuple[str, bool]:
        existing = None
        if dedupe_key:
            existing = target_session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
        if existing is not None:
            if requeue_terminal and existing.status not in {"queued", "running"}:
                existing.kind = kind
                existing.payload = payload
                existing.status = "queued"
                existing.priority = priority
                existing.run_after = scheduled_at
                existing.attempts = 0
                existing.max_attempts = max_attempts
                existing.locked_by = None
                existing.locked_at = None
                existing.heartbeat_at = None
                existing.result = None
                existing.error = None
                existing.updated_at = now
                target_session.flush()
                return existing.id, True
            return existing.id, False
        target_session.add(Job(
            id=job_id,
            kind=kind,
            payload=payload,
            status="queued",
            priority=priority,
            run_after=scheduled_at,
            attempts=0,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
            dedupe_key=dedupe_key,
        ))
        target_session.flush()
        return job_id, True

    if session is not None:
        return persist(session)[0]

    try:
        with _session() as session:
            persisted_id, should_inline_job = persist(session)
    except IntegrityError:
        if not dedupe_key:
            raise
        with _session() as session:
            existing = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
            if existing:
                return existing.id
        raise

    job_id = persisted_id

    inline_enabled = (
        os.environ.get("JOBS_INLINE", "false").strip().lower() in {"1", "true", "yes", "on"}
        if inline is None else inline
    )
    if should_inline_job and scheduled_at <= now and inline_enabled:
        run_inline_job(job_id)
    return job_id


def enqueue_exclusive(
    kind: str,
    payload: dict[str, Any],
    *,
    statuses: Iterable[str] = ("queued", "running"),
    run_after: dt.datetime | None = None,
    max_attempts: int = 3,
) -> tuple[str, bool]:
    """Atomically allow at most one active job of a kind across API processes."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")
    now = _now()
    scheduled_at = run_after or now
    if scheduled_at.tzinfo is None or scheduled_at.tzinfo.utcoffset(scheduled_at) is None:
        scheduled_at = scheduled_at.replace(tzinfo=dt.UTC)
    active_statuses = list(statuses)
    job_id = uuid.uuid4().hex
    should_inline = False
    with _session() as session:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif dialect == "postgresql":
            lock_suffix = int.from_bytes(hashlib.sha256(kind.encode("utf-8")).digest()[:4], "big")
            lock_key = (0x4A4F4253 << 32) | lock_suffix
            session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        active = (
            session.query(Job)
            .filter(Job.kind == kind, Job.status.in_(active_statuses))
            .order_by(Job.created_at.desc())
            .first()
        )
        if active:
            return active.id, False
        session.add(Job(
            id=job_id,
            kind=kind,
            payload=payload,
            status="queued",
            priority=0,
            run_after=scheduled_at,
            attempts=0,
            max_attempts=max_attempts,
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        should_inline = scheduled_at <= now and os.environ.get("JOBS_INLINE", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
    if should_inline:
        run_inline_job(job_id)
    return job_id, True


def run_inline_job(job_id: str) -> None:
    worker_id = f"inline-{os.getpid()}"
    with _session() as session:
        now = _now()
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(
                status="running",
                attempts=Job.attempts + 1,
                locked_by=worker_id,
                locked_at=now,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            return
        job = session.get(Job, job_id)
        kind, payload = job.kind, job.payload
    try:
        from .registry import get_handler, PermanentJobError

        value = get_handler(kind)(payload, job_id)
        complete(job_id, value, worker_id=worker_id)
    except PermanentJobError as exc:
        logger.info("Inline job %s (%s) failed permanently: %s", job_id, kind, exc)
        fail(job_id, exc, worker_id=worker_id, retry=False)
    except BaseException as exc:
        logger.exception("Inline job %s (%s) failed", job_id, kind)
        fail(job_id, exc, worker_id=worker_id)


def reclaim_expired(*, lease_timeout: int | None = None) -> int:
    timeout = lease_timeout or int(os.environ.get("JOBS_LEASE_TIMEOUT", "300"))
    cutoff = _now() - dt.timedelta(seconds=timeout)
    now = _now()
    with _session() as session:
        return _reclaim_expired_in_session(session, cutoff=cutoff, now=now)


def _reclaim_expired_in_session(session: Session, *, cutoff: dt.datetime, now: dt.datetime) -> int:
    """Release stale leases and stop retrying jobs whose attempts are exhausted."""
    exhausted = Job.attempts >= Job.max_attempts
    result = session.execute(
        update(Job)
        .where(
            Job.status == "running",
            func.coalesce(Job.heartbeat_at, Job.locked_at) < cutoff,
        )
        .values(
            status=case((exhausted, "failed"), else_="queued"),
            locked_by=None,
            locked_at=None,
            heartbeat_at=None,
            run_after=now,
            updated_at=now,
            error=case(
                (exhausted, "Worker 租约超时，任务已达到最大尝试次数"),
                else_="Worker 租约超时，任务已重新入队",
            ),
        )
    )
    return int(result.rowcount or 0)


def claim_next(worker_id: str, kinds: Iterable[str] | None = None) -> dict[str, Any] | None:
    """Atomically claim the next due task; SQLite claims are serialized locally."""
    kind_list = list(kinds or [])
    if not kind_list:
        return None
    now = _now()
    cutoff = now - dt.timedelta(seconds=int(os.environ.get("JOBS_LEASE_TIMEOUT", "300")))
    with _session() as session:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _reclaim_expired_in_session(session, cutoff=cutoff, now=now)
        query = select(Job.id).where(
            Job.status == "queued",
            Job.kind.in_(kind_list),
            Job.run_after <= now,
        ).order_by(Job.priority.desc(), Job.run_after.asc(), Job.created_at.asc())
        if dialect == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job_id = session.execute(query.limit(1)).scalar_one_or_none()
        if job_id is None:
            return None
        updated = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(
                status="running",
                attempts=Job.attempts + 1,
                locked_by=worker_id,
                locked_at=now,
                heartbeat_at=now,
                updated_at=now,
                error=None,
            )
        )
        if updated.rowcount != 1:
            return None
        job = session.get(Job, job_id)
        return _job_dict(job)


def heartbeat(job_id: str, worker_id: str | None = None) -> bool:
    now = _now()
    condition = [Job.id == job_id, Job.status == "running"]
    if worker_id:
        condition.append(Job.locked_by == worker_id)
    with _session() as session:
        result = session.execute(
            update(Job).where(*condition).values(heartbeat_at=now, updated_at=now)
        )
        return result.rowcount == 1


def update_result(job_id: str, result: dict[str, Any], *, worker_id: str | None = None) -> bool:
    """Replace result JSON while a handler reports progress to status clients."""
    condition = [Job.id == job_id, Job.status == "running"]
    if worker_id:
        condition.append(Job.locked_by == worker_id)
    with _session() as session:
        updated = session.execute(
            update(Job).where(*condition).values(result=result, updated_at=_now())
        )
        return updated.rowcount == 1


def complete(job_id: str, result: Any = None, *, worker_id: str | None = None) -> bool:
    condition = [Job.id == job_id, Job.status == "running"]
    if worker_id:
        condition.append(Job.locked_by == worker_id)
    with _session() as session:
        updated = session.execute(
            update(Job)
            .where(*condition)
            .values(
                status="succeeded",
                result=result if isinstance(result, (dict, list)) or result is None else {"value": result},
                error=None,
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                updated_at=_now(),
            )
        )
        return updated.rowcount == 1


def mark_succeeded(job_id: str, result: dict[str, Any] | None = None) -> bool:
    """Mark a related job successful after an out-of-band artifact rerender."""
    with _session() as session:
        updated = session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status="succeeded",
                result=result,
                error=None,
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                updated_at=_now(),
            )
        )
        return updated.rowcount == 1


def fail(
    job_id: str,
    error: BaseException | str,
    *,
    worker_id: str | None = None,
    retry: bool = True,
    backoff_seconds: int | None = None,
) -> bool:
    condition = [Job.id == job_id, Job.status == "running"]
    if worker_id:
        condition.append(Job.locked_by == worker_id)
    with _session() as session:
        job = session.query(Job).filter(*condition).with_for_update().first()
        if job is None:
            return False
        message = str(error)
        can_retry = retry and int(job.attempts or 0) < int(job.max_attempts or 1)
        delay = backoff_seconds
        if delay is None:
            delay = min(3600, 5 * (2 ** max(0, int(job.attempts or 1) - 1)))
        now = _now()
        job.status = "queued" if can_retry else "failed"
        job.error = message[:4000]
        job.run_after = now + dt.timedelta(seconds=delay) if can_retry else job.run_after
        job.locked_by = None
        job.locked_at = None
        job.heartbeat_at = None
        job.updated_at = now
        return True


def cancel(job_id: str) -> bool:
    now = _now()
    with _session() as session:
        updated = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["queued", "running"]))
            .values(
                status="cancelled",
                locked_by=None,
                locked_at=None,
                heartbeat_at=None,
                updated_at=now,
            )
        )
        return updated.rowcount == 1


def get_job(job_id: str) -> dict[str, Any] | None:
    with _session() as session:
        row = session.get(Job, job_id)
        return _job_dict(row) if row else None


def cleanup_terminal_jobs(*, older_than_days: int = 30, limit: int = 5000) -> int:
    """Delete old terminal queue rows; running/queued jobs are never touched."""
    if older_than_days <= 0:
        return 0
    import datetime as dt

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=older_than_days)
    with _session() as session:
        rows = (
            session.query(Job)
            .filter(
                Job.status.in_(("succeeded", "failed", "cancelled")),
                Job.updated_at < cutoff,
            )
            .limit(limit)
            .all()
        )
        for row in rows:
            session.delete(row)
        return len(rows)


def find_jobs(
    *, kind: str | None = None, statuses: Iterable[str] | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    with _session() as session:
        query = session.query(Job)
        if kind:
            query = query.filter(Job.kind == kind)
        status_list = list(statuses or [])
        if status_list:
            query = query.filter(Job.status.in_(status_list))
        rows = query.order_by(Job.created_at.desc()).limit(limit).all()
        return [_job_dict(row) for row in rows]


def find_by_dedupe_key(dedupe_key: str) -> dict[str, Any] | None:
    with _session() as session:
        row = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
        return _job_dict(row) if row else None


def cancel_by_dedupe_key(dedupe_key: str) -> bool:
    job = find_by_dedupe_key(dedupe_key)
    return cancel(job["id"]) if job else False


def _json_safe(value: Any) -> Any:
    """Test helper and CLI consumers can serialize timestamps consistently."""
    return json.loads(json.dumps(value, default=lambda item: item.isoformat()))
