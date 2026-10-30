"""Publish targets, durable queueing, schedules, cancellation, and execution."""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import random

from sqlalchemy import text, update

from paper_library.db import get_session
from paper_library.models import ArticleDraft, ChannelAccount, PublishRecord
from paper_library.time_utils import format_api_datetime, parse_api_datetime
from publisher.credential_store import decrypt_credential

from .draft_images import draft_image_context
from .publisher_runtime import run_publisher_isolated

logger = logging.getLogger(__name__)

PUBLISH_TRANSITIONS = {
    "scheduled": {"pending", "cancelled"},
    "pending": {"publishing", "cancelled"},
    "publishing": {"success", "failed", "cancelled"},
    "success": set(),
    "failed": set(),
    "cancelled": set(),
}


class InvalidPublishTargets(ValueError):
    def __init__(self, invalid_ids: list[int]):
        self.invalid_ids = sorted(set(invalid_ids))
        message = f"Inactive or missing accounts: {self.invalid_ids}" if self.invalid_ids else "At least one publish account is required"
        super().__init__(message)


class MissingDrafts(ValueError):
    def __init__(self, missing_ids: list[int]):
        self.missing_ids = list(dict.fromkeys(missing_ids))
        super().__init__(f"Drafts not found: {self.missing_ids}")


class PublishRecordNotFound(LookupError):
    pass


class InvalidPublishTransition(ValueError):
    pass


def transition_publish_record(
    session,
    record_id: int,
    target_status: str,
    *,
    url: str | None = None,
    error: str | None = None,
    now: dt.datetime | None = None,
) -> bool:
    """Atomically apply an allowed record transition; cancellation wins races."""
    allowed_from = [source for source, targets in PUBLISH_TRANSITIONS.items() if target_status in targets]
    if not allowed_from:
        raise InvalidPublishTransition(f"No publish record transition leads to {target_status}")
    current_time = now or dt.datetime.now(dt.UTC)
    values = {
        "status": target_status,
        "updated_at": current_time,
        "error": error,
    }
    if target_status in {"success", "failed"}:
        values["url"] = url
    if target_status == "success":
        values["published_at"] = current_time
    changed = session.execute(
        update(PublishRecord)
        .where(PublishRecord.id == record_id, PublishRecord.status.in_(allowed_from))
        .values(**values)
    )
    return changed.rowcount == 1


def resolve_publish_targets(account_ids: list[int], *, workspace: str, config_dir: str) -> list[dict]:
    requested = list(dict.fromkeys(account_ids))
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        accounts = session.query(ChannelAccount).filter(ChannelAccount.id.in_(requested)).all() if requested else []
        active = [account for account in accounts if account.is_active]
        by_id = {account.id: account for account in active}
    invalid = [account_id for account_id in requested if account_id not in by_id]
    if invalid:
        raise InvalidPublishTargets(invalid)
    targets = [
        {"id": account.id, "channel": account.channel, "account_name": account.account_name}
        for account_id in requested
        for account in [by_id[account_id]]
    ]
    if not targets:
        raise InvalidPublishTargets([])
    return targets


def _validate_draft_ids(session, draft_ids: list[int]) -> list[int]:
    requested = list(dict.fromkeys(draft_ids))
    existing = {row[0] for row in session.query(ArticleDraft.id).filter(ArticleDraft.id.in_(requested)).all()} if requested else set()
    missing = [draft_id for draft_id in requested if draft_id not in existing]
    if missing:
        raise MissingDrafts(missing)
    return requested


def _queue_publish_task(session, enqueue, *, draft_id: int, account: dict, workspace: str, config_dir: str,
                        idempotency_key: str | None) -> dict:
    record = None
    if idempotency_key is not None:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "sqlite" and not session.info.get("publish_idempotency_write_lock"):
            connection = session.connection()
            dbapi_connection = connection.connection.driver_connection
            if not dbapi_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            session.info["publish_idempotency_write_lock"] = True
        elif dialect == "postgresql":
            lock_material = f"publish:{idempotency_key}".encode("utf-8")
            lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
        duplicate_query = session.query(PublishRecord).filter(
            PublishRecord.draft_id == draft_id,
            PublishRecord.idempotency_key == idempotency_key,
        )
        duplicate_query = duplicate_query.filter(PublishRecord.account_id == account["id"])
        record = duplicate_query.first()
        if record is not None:
            created_at = record.created_at or dt.datetime.min.replace(tzinfo=dt.UTC)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=dt.UTC)
            if dt.datetime.now(dt.UTC) - created_at <= dt.timedelta(hours=24):
                job_id = enqueue(
                    "publish_draft",
                    {"record_id": record.id, "workspace": workspace, "config_dir": config_dir},
                    dedupe_key=f"publish-draft-record:{record.id}",
                    session=session,
                    inline=False,
                )
                return {
                    "record_id": record.id,
                    "draft_id": draft_id,
                    "channel": record.channel,
                    "account_name": record.account_name,
                    "status": record.status,
                    "job_id": job_id,
                }
            record.idempotency_key = None
    record = PublishRecord(
        draft_id=draft_id,
        account_id=account["id"],
        channel=account["channel"],
        account_name=account["account_name"],
        status="pending",
        idempotency_key=idempotency_key,
    )
    session.add(record)
    session.flush()
    job_id = enqueue(
        "publish_draft",
        {"record_id": record.id, "workspace": workspace, "config_dir": config_dir},
        dedupe_key=f"publish-draft-record:{record.id}",
        session=session,
        inline=False,
    )
    return {
        "record_id": record.id,
        "draft_id": draft_id,
        "channel": account["channel"],
        "account_name": account["account_name"],
        "status": "pending",
        "job_id": job_id,
    }


def enqueue_publish(
    draft_ids: list[int], account_ids: list[int], *,
    idempotency_key: str | None, workspace: str, config_dir: str, enqueue_fn=None,
) -> list[dict]:
    targets = resolve_publish_targets(account_ids, workspace=workspace, config_dir=config_dir)
    if enqueue_fn is None:
        from worker.queue import enqueue as enqueue_fn
    tasks = []
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        ids = _validate_draft_ids(session, draft_ids)
        for draft_id in ids:
            for account in targets:
                tasks.append(_queue_publish_task(
                    session,
                    enqueue_fn,
                    draft_id=draft_id,
                    account=account,
                    workspace=workspace,
                    config_dir=config_dir,
                    idempotency_key=idempotency_key,
                ))
    return tasks


def schedule_records(draft_ids: list[int], account_ids: list[int], start_at: dt.datetime, *,
                     interval_hours: float = 0, jitter_hours: float = 0,
                     workspace: str, config_dir: str) -> dict:
    targets = resolve_publish_targets(account_ids, workspace=workspace, config_dir=config_dir)
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        ids = _validate_draft_ids(session, draft_ids)
        times: list[tuple[int, dt.datetime]] = []
        current = start_at
        for index, draft_id in enumerate(ids):
            times.append((draft_id, current))
            if index < len(ids) - 1:
                current += dt.timedelta(hours=interval_hours + random.uniform(-jitter_hours, jitter_hours))
        created = 0
        for draft_id, scheduled_at in times:
            for account in targets:
                session.add(PublishRecord(
                    draft_id=draft_id,
                    account_id=account["id"],
                    channel=account["channel"],
                    account_name=account["account_name"],
                    status="scheduled",
                    scheduled_at=scheduled_at,
                ))
                created += 1
        session.flush()
    return {
        "draft_count": len(ids),
        "account_count": len(targets),
        "scheduled_records": created,
        "times": times,
        "first_at": format_api_datetime(times[0][1]) if times else None,
        "last_at": format_api_datetime(times[-1][1]) if times else None,
    }


def cancel_publish_record(record_id: int, *, workspace: str, config_dir: str) -> None:
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        record = session.get(PublishRecord, record_id)
        if record is None:
            raise PublishRecordNotFound(record_id)
        if record.status not in {"pending", "publishing", "scheduled"}:
            raise InvalidPublishTransition(f"Cannot cancel publish record in status {record.status}")
        if not transition_publish_record(session, record_id, "cancelled"):
            raise InvalidPublishTransition("Publish record changed while cancellation was in progress")
    from worker.queue import cancel_by_dedupe_key
    cancel_by_dedupe_key(f"publish-draft-record:{record_id}")


def cancel_publish_records_for_draft(draft_id: int, *, workspace: str, config_dir: str) -> int:
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = session.query(PublishRecord).filter(
            PublishRecord.draft_id == draft_id,
            PublishRecord.status.in_(["pending", "publishing", "scheduled"]),
        ).all()
        record_ids = [record.id for record in records]
        count = sum(transition_publish_record(session, record_id, "cancelled") for record_id in record_ids)
    from worker.queue import cancel_by_dedupe_key
    for record_id in record_ids:
        cancel_by_dedupe_key(f"publish-draft-record:{record_id}")
    return count


def clear_terminal_publish_records(*, workspace: str, config_dir: str) -> int:
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = session.query(PublishRecord).filter(PublishRecord.status.in_(["success", "failed", "cancelled"])).all()
        for record in records:
            record.hidden = True
            session.add(record)
        return len(records)


def list_outbox(*, workspace: str, config_dir: str) -> dict:
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    from worker.queue import find_by_dedupe_key
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        records = session.query(PublishRecord).filter(
            PublishRecord.status.in_(["pending", "publishing", "success", "failed", "scheduled", "cancelled"]),
            PublishRecord.hidden.is_(False),
            PublishRecord.created_at >= cutoff,
        ).order_by(PublishRecord.created_at.desc()).all()
        draft_ids = {record.draft_id for record in records}
        drafts = session.query(ArticleDraft).filter(ArticleDraft.id.in_(draft_ids)).all() if draft_ids else []
        draft_map = {}
        for record in records:
            job = find_by_dedupe_key(f"publish-draft-record:{record.id}")
            status, error = record.status, record.error
            if job and job["status"] == "failed" and status in {"pending", "publishing"}:
                status, error = "failed", error or "发布任务未能完成，请查看服务端日志后重试"
            elif job and job["status"] == "cancelled" and status != "cancelled":
                status = "cancelled"
            if record.draft_id not in draft_map:
                draft = next((item for item in drafts if item.id == record.draft_id), None)
                draft_map[record.draft_id] = {
                    "draft_id": record.draft_id,
                    "draft_title": draft.title if draft else None,
                    "scheduled_at": format_api_datetime(record.scheduled_at),
                    "accounts": [],
                    "record_ids": [],
                }
            draft_map[record.draft_id]["accounts"].append({
                "channel": record.channel,
                "account_name": record.account_name,
                "record_id": record.id,
                "status": status,
                "error": error,
                "url": record.url,
                "published_at": format_api_datetime(record.published_at),
                "job_id": job["id"] if job else None,
                "job_status": job["status"] if job else None,
            })
            draft_map[record.draft_id]["record_ids"].append(record.id)
        return {"items": list(draft_map.values())}


def _update_draft_published(draft_id: int, workspace: str, config_dir: str) -> None:
    try:
        with get_session(workspace=workspace, config_dir=config_dir) as session:
            draft = session.get(ArticleDraft, draft_id)
            if draft and draft.status != "published":
                draft.status = "published"
                draft.published_at = dt.datetime.now(dt.UTC)
                session.add(draft)
    except Exception:
        logger.exception("Failed to update draft %s to published", draft_id)


def execute_publish_record(record_id: int, workspace: str, config_dir: str) -> dict:
    """Claim and execute one durable publish record."""
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        record = session.get(PublishRecord, record_id)
        if record is None:
            return {"status": "missing"}
        if not transition_publish_record(session, record_id, "publishing"):
            current = session.get(PublishRecord, record_id)
            return {"status": current.status if current else "missing", "error": current.error if current else None}
        draft_id, account_id = record.draft_id, record.account_id
        draft = session.get(ArticleDraft, draft_id)
        if draft is None:
            transition_publish_record(session, record_id, "failed", error="Draft not found")
            return {"status": "failed", "error": "Draft not found"}
        title, content = draft.title, draft.content
        images = draft_image_context(draft, session, workspace)

    with get_session(workspace=workspace, config_dir=config_dir) as session:
        account = session.get(ChannelAccount, account_id) if account_id is not None else None
        if account is None:
            with get_session(workspace=workspace, config_dir=config_dir) as draft_session:
                transition_publish_record(draft_session, record_id, "failed", error="Account not found")
            return {"status": "failed", "error": "Account not found"}
        account_data = {
            "id": account.id,
            "channel": account.channel,
            "account_name": account.account_name,
            "credential_type": account.credential_type,
            "credential": decrypt_credential(account.credential),
            "workspace": workspace,
            "config_dir": config_dir,
        }

    def is_cancelled() -> bool:
        try:
            with get_session(workspace=workspace, config_dir=config_dir) as session:
                current = session.get(PublishRecord, record_id)
                return current is None or current.status == "cancelled"
        except Exception:
            logger.warning("检查发布取消状态失败", exc_info=True)
            return False

    result = run_publisher_isolated(
        account=account_data,
        draft_id=draft_id,
        title=title,
        content=content,
        cover_images=images["cover_images"],
        image_doi_map=images["image_doi_map"],
        is_cancelled=is_cancelled,
    )
    if result.get("error") == "已取消":
        return {"status": "cancelled", "error": "已取消"}
    status = "success" if result.get("success") else "failed"
    with get_session(workspace=workspace, config_dir=config_dir) as session:
        changed = transition_publish_record(
            session,
            record_id,
            status,
            url=result.get("url"),
            error=result.get("error"),
        )
    if changed and status == "success":
        _update_draft_published(draft_id, workspace, config_dir)
    return {"status": "cancelled" if not changed else status, "url": result.get("url"), "error": result.get("error")}


def parse_scheduled_datetime(value: str) -> dt.datetime:
    return parse_api_datetime(value, naive_as_local=True)
