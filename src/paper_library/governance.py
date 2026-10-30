"""Transactional quota, approval, artifact, and audit services."""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sqlalchemy import func, or_, text

from .db import get_session
from .governance_models import (
    AccessRequest,
    AuditEvent,
    DownloadArtifact,
    QuotaGrant,
    QuotaPolicy,
    QuotaReservation,
    QuotaUsage,
    QuotaUsageItem,
)
from .time_utils import (
    BUSINESS_TIMEZONE,
    local_business_date,
    ensure_utc,
    format_api_datetime,
    utc_now,
)


SENSITIVE_KEY = re.compile(r"password|token|cookie|secret|api[_-]?key|authorization", re.I)
RESERVATION_TIMEOUT = dt.timedelta(minutes=30)


def _lock_quota_write(session, *, lock_key: str) -> None:
    """Serialize quota mutations for one actor/day on SQLite and PostgreSQL."""
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    elif dialect == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def sanitize_details(value: Any, *, depth: int = 0) -> Any:
    """Keep useful metadata while excluding credentials and large content."""
    if depth > 4:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if SENSITIVE_KEY.search(str(key)) else sanitize_details(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_details(item, depth=depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def actor_value(actor: Mapping[str, Any], key: str, fallback: str = "") -> str:
    value = actor.get(key, fallback)
    return str(value) if value is not None else fallback


def record_audit(
    actor: Mapping[str, Any],
    *,
    action: str,
    resource_type: str,
    result: str,
    resource_id: str | int | None = None,
    resource_count: int = 0,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: Mapping[str, Any] | None = None,
    occurred_at: dt.datetime | None = None,
) -> str:
    now = ensure_utc(occurred_at or utc_now())
    event_id = uuid.uuid4().hex
    with get_session() as session:
        session.add(AuditEvent(
            event_id=event_id,
            occurred_at_utc=now,
            business_date=local_business_date(now),
            actor_id=actor_value(actor, "actor_id", "system:unknown"),
            username=actor_value(actor, "username", "unknown"),
            role=actor_value(actor, "role", "unknown"),
            display_name=actor_value(actor, "name") or actor_value(actor, "display_name"),
            department=actor_value(actor, "department"),
            login_source=actor_value(actor, "login_source", "unknown"),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            resource_count=max(0, int(resource_count)),
            result=result,
            request_id=request_id,
            ip_address=(ip_address or "")[:64] or None,
            user_agent=(user_agent or "")[:500] or None,
            details_json=_json(sanitize_details(details or {})),
        ))
    return event_id


@dataclass(frozen=True)
class QuotaSnapshot:
    actor_id: str
    business_date: dt.date
    timezone: str
    enabled: bool
    limit: int | None
    used: int
    reserved: int
    remaining: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "business_date": self.business_date.isoformat(),
            "timezone": self.timezone,
            "enabled": self.enabled,
            "limit": self.limit,
            "used": self.used,
            "reserved": self.reserved,
            "remaining": self.remaining,
        }


class QuotaExceeded(Exception):
    def __init__(self, snapshot: QuotaSnapshot, requested_new: int, paper_ids: list[int]):
        self.snapshot = snapshot
        self.requested_new = requested_new
        self.paper_ids = paper_ids
        super().__init__("paper quota exceeded")

    def detail(self) -> dict[str, Any]:
        return {
            "code": "QUOTA_EXCEEDED",
            "message": "今日论文导出/下载额度不足",
            **self.snapshot.as_dict(),
            "requested_new": self.requested_new,
            "requestable": True,
            "paper_ids": self.paper_ids,
        }


@dataclass(frozen=True)
class ReservationHandle:
    reservation_id: str | None
    actor_id: str
    action: str
    paper_ids: tuple[int, ...]
    new_paper_ids: tuple[int, ...]
    enforced: bool
    already_completed: bool = False


def _unique_paper_ids(paper_ids: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(int(paper_id) for paper_id in paper_ids if int(paper_id) > 0))


def _get_policy(session, role: str) -> QuotaPolicy | None:
    return session.query(QuotaPolicy).filter_by(
        role=role, resource_type="paper", period_type="day"
    ).first()


def get_quota_snapshot(actor: Mapping[str, Any], *, now: dt.datetime | None = None) -> QuotaSnapshot:
    actor_id = actor_value(actor, "actor_id")
    current = ensure_utc(now or utc_now())
    business_date = local_business_date(current)
    with get_session() as session:
        policy = _get_policy(session, actor_value(actor, "role"))
        usage = session.query(QuotaUsage).filter_by(
            actor_id=actor_id, business_date=business_date, resource_type="paper"
        ).first()
        enabled = bool(policy and policy.enabled)
        base_limit = int(policy.limit_value) if policy else 0
        extra = int(session.query(func.coalesce(func.sum(QuotaGrant.granted_count), 0)).filter(
            QuotaGrant.actor_id == actor_id,
            QuotaGrant.business_date == business_date,
            QuotaGrant.grant_type == "daily_extra",
            QuotaGrant.expires_at_utc > current,
        ).scalar() or 0)
        if usage and usage.extra_granted != extra:
            usage.extra_granted = extra
        limit = base_limit + extra if enabled else None
        used = int(usage.used_count) if usage else 0
        reserved = int(usage.reserved_count) if usage else 0
        remaining = max(0, limit - used - reserved) if limit is not None else None
        return QuotaSnapshot(
            actor_id=actor_id,
            business_date=business_date,
            timezone=policy.timezone if policy else BUSINESS_TIMEZONE,
            enabled=enabled,
            limit=limit,
            used=used,
            reserved=reserved,
            remaining=remaining,
        )


def _active_exact_grant_papers(session, actor_id: str, business_date: dt.date, action: str, now: dt.datetime) -> set[int]:
    grants = session.query(QuotaGrant).filter(
        QuotaGrant.actor_id == actor_id,
        QuotaGrant.business_date == business_date,
        QuotaGrant.grant_type == "exact_scope",
        QuotaGrant.expires_at_utc > now,
        or_(QuotaGrant.action == action, QuotaGrant.action.is_(None)),
    ).all()
    result: set[int] = set()
    for grant in grants:
        result.update(int(item) for item in _loads(grant.paper_ids_json, []))
    return result


def _active_daily_extra(session, actor_id: str, business_date: dt.date, now: dt.datetime) -> int:
    return int(session.query(func.coalesce(func.sum(QuotaGrant.granted_count), 0)).filter(
        QuotaGrant.actor_id == actor_id,
        QuotaGrant.business_date == business_date,
        QuotaGrant.grant_type == "daily_extra",
        QuotaGrant.expires_at_utc > now,
    ).scalar() or 0)


def reserve_papers(
    actor: Mapping[str, Any],
    paper_ids: Iterable[int],
    *,
    action: str,
    reservation_id: str | None = None,
    now: dt.datetime | None = None,
) -> ReservationHandle:
    ids = _unique_paper_ids(paper_ids)
    actor_id = actor_value(actor, "actor_id")
    role = actor_value(actor, "role")
    current = ensure_utc(now or utc_now())
    business_date = local_business_date(current)
    if not ids:
        return ReservationHandle(None, actor_id, action, (), (), False)

    with get_session() as session:
        _lock_quota_write(session, lock_key=f"quota:{actor_id}:{business_date}:paper")

        policy = _get_policy(session, role)
        if not policy or not policy.enabled:
            return ReservationHandle(None, actor_id, action, tuple(ids), (), False)

        rid = (reservation_id or uuid.uuid4().hex)[:64]
        existing_reservation = session.query(QuotaReservation).filter_by(reservation_id=rid).first()
        if existing_reservation:
            if existing_reservation.actor_id != actor_id or existing_reservation.action != action:
                raise ValueError("reservation id belongs to another operation")
            existing_ids = tuple(int(item) for item in _loads(existing_reservation.paper_ids_json, []))
            if existing_ids != tuple(ids):
                raise ValueError("reservation id was reused with different papers")
            return ReservationHandle(
                rid,
                actor_id,
                action,
                existing_ids,
                tuple(int(item) for item in _loads(existing_reservation.new_paper_ids_json, [])),
                True,
                already_completed=existing_reservation.state == "confirmed",
            )

        usage = session.query(QuotaUsage).filter_by(
            actor_id=actor_id, business_date=business_date, resource_type="paper"
        ).first()
        if usage is None:
            usage = QuotaUsage(
                actor_id=actor_id,
                business_date=business_date,
                resource_type="paper",
                base_limit=int(policy.limit_value),
            )
            session.add(usage)
            session.flush()

        usage.extra_granted = _active_daily_extra(session, actor_id, business_date, current)

        existing_items = {
            item.paper_id: item
            for item in session.query(QuotaUsageItem).filter(
                QuotaUsageItem.usage_id == usage.id,
                QuotaUsageItem.paper_id.in_(ids),
            ).all()
        }
        exact_granted = _active_exact_grant_papers(session, actor_id, business_date, action, current)
        new_ids = [
            paper_id for paper_id in ids
            if paper_id not in exact_granted
            and (paper_id not in existing_items or existing_items[paper_id].state == "released")
        ]
        total_limit = int(usage.base_limit) + int(usage.extra_granted)
        remaining = max(0, total_limit - int(usage.used_count) - int(usage.reserved_count))
        if len(new_ids) > remaining:
            snapshot = QuotaSnapshot(
                actor_id=actor_id,
                business_date=business_date,
                timezone=policy.timezone,
                enabled=True,
                limit=total_limit,
                used=int(usage.used_count),
                reserved=int(usage.reserved_count),
                remaining=remaining,
            )
            raise QuotaExceeded(snapshot, len(new_ids), ids)

        for paper_id in new_ids:
            item = existing_items.get(paper_id)
            if item is None:
                item = QuotaUsageItem(
                    usage_id=usage.id,
                    paper_id=paper_id,
                    state="reserved",
                    reservation_id=rid,
                    first_action=action,
                )
                session.add(item)
            else:
                item.state = "reserved"
                item.reservation_id = rid
                item.updated_at_utc = current
        usage.reserved_count = int(usage.reserved_count) + len(new_ids)
        usage.updated_at_utc = current
        session.add(QuotaReservation(
            reservation_id=rid,
            actor_id=actor_id,
            business_date=business_date,
            action=action,
            state="reserved",
            paper_ids_json=_json(ids),
            new_paper_ids_json=_json(new_ids),
        ))
        return ReservationHandle(rid, actor_id, action, tuple(ids), tuple(new_ids), True)


def confirm_reservation(handle: ReservationHandle, *, artifact_id: str | None = None) -> None:
    if not handle.enforced or not handle.reservation_id or handle.already_completed:
        return
    current = utc_now()
    with get_session() as session:
        _lock_quota_write(session, lock_key=f"quota:{handle.actor_id}:{local_business_date(current)}:paper")
        reservation = session.query(QuotaReservation).filter_by(reservation_id=handle.reservation_id).first()
        if not reservation or reservation.state != "reserved":
            return
        usage = session.query(QuotaUsage).filter_by(
            actor_id=handle.actor_id,
            business_date=reservation.business_date,
            resource_type="paper",
        ).one()
        changed = session.query(QuotaUsageItem).filter(
            QuotaUsageItem.usage_id == usage.id,
            QuotaUsageItem.reservation_id == handle.reservation_id,
            QuotaUsageItem.state == "reserved",
        ).all()
        for item in changed:
            item.state = "confirmed"
            item.updated_at_utc = current
        count = len(changed)
        usage.reserved_count = max(0, int(usage.reserved_count) - count)
        usage.used_count = int(usage.used_count) + count
        usage.updated_at_utc = current
        reservation.state = "confirmed"
        reservation.artifact_id = artifact_id
        reservation.updated_at_utc = current


def release_reservation(handle: ReservationHandle, *, error_code: str | None = None) -> None:
    if not handle.enforced or not handle.reservation_id or handle.already_completed:
        return
    current = utc_now()
    with get_session() as session:
        _lock_quota_write(session, lock_key=f"quota:{handle.actor_id}:{local_business_date(current)}:paper")
        reservation = session.query(QuotaReservation).filter_by(reservation_id=handle.reservation_id).first()
        if not reservation or reservation.state != "reserved":
            return
        usage = session.query(QuotaUsage).filter_by(
            actor_id=handle.actor_id,
            business_date=reservation.business_date,
            resource_type="paper",
        ).one()
        changed = session.query(QuotaUsageItem).filter(
            QuotaUsageItem.usage_id == usage.id,
            QuotaUsageItem.reservation_id == handle.reservation_id,
            QuotaUsageItem.state == "reserved",
        ).all()
        for item in changed:
            item.state = "released"
            item.updated_at_utc = current
        usage.reserved_count = max(0, int(usage.reserved_count) - len(changed))
        usage.updated_at_utc = current
        reservation.state = "released"
        reservation.error_code = (error_code or "generation_failed")[:80]
        reservation.updated_at_utc = current


def release_stale_reservations(*, now: dt.datetime | None = None) -> int:
    current = ensure_utc(now or utc_now())
    cutoff = current - RESERVATION_TIMEOUT
    with get_session() as session:
        stale_ids = [
            row.reservation_id
            for row in session.query(QuotaReservation).filter(
                QuotaReservation.state == "reserved",
                QuotaReservation.created_at_utc < cutoff,
            ).all()
        ]
    for rid in stale_ids:
        with get_session() as session:
            reservation = session.query(QuotaReservation).filter_by(reservation_id=rid).first()
            if not reservation:
                continue
            handle = ReservationHandle(
                rid,
                reservation.actor_id,
                reservation.action,
                tuple(_loads(reservation.paper_ids_json, [])),
                tuple(_loads(reservation.new_paper_ids_json, [])),
                True,
            )
        release_reservation(handle, error_code="reservation_timeout")
        record_audit(
            {
                "actor_id": handle.actor_id,
                "username": "quota-owner",
                "role": "unknown",
                "login_source": "system",
            },
            action="governance.quota_release",
            resource_type="quota_reservation",
            resource_id=rid,
            resource_count=len(handle.new_paper_ids),
            result="success",
            details={"reason": "reservation_timeout"},
        )
    return len(stale_ids)


def expire_access_requests(*, now: dt.datetime | None = None) -> int:
    """Mark approved requests whose temporary grant has elapsed as expired."""
    current = ensure_utc(now or utc_now())
    expired: list[tuple[int, str]] = []
    with get_session() as session:
        rows = session.query(AccessRequest).filter(
            AccessRequest.status == "approved",
            AccessRequest.expires_at_utc.is_not(None),
            AccessRequest.expires_at_utc <= current,
        ).all()
        for row in rows:
            row.status = "expired"
            row.version += 1
            expired.append((row.id, row.requester_actor_id))
    for request_id, requester_actor_id in expired:
        record_audit(
            {
                "actor_id": "system:scheduler",
                "username": "scheduler",
                "role": "system",
                "login_source": "system",
            },
            action="governance.request_expire",
            resource_type="access_request",
            resource_id=request_id,
            result="success",
            details={"requester_actor_id": requester_actor_id},
            occurred_at=current,
        )
    return len(expired)


def register_artifact(
    actor: Mapping[str, Any],
    *,
    action: str,
    filename: str,
    paper_ids: Iterable[int],
    expires_at: dt.datetime,
    file_id: str | None = None,
    content_type: str | None = None,
    reservation_id: str | None = None,
) -> str:
    artifact_id = uuid.uuid4().hex
    with get_session() as session:
        session.add(DownloadArtifact(
            artifact_id=artifact_id,
            file_id=file_id,
            actor_id=actor_value(actor, "actor_id"),
            action=action,
            filename=filename,
            content_type=content_type,
            paper_ids_json=_json(_unique_paper_ids(paper_ids)),
            reservation_id=reservation_id,
            expires_at_utc=ensure_utc(expires_at),
        ))
    return artifact_id


def confirm_output_artifact(
    handle: ReservationHandle,
    actor: Mapping[str, Any],
    *,
    action: str,
    filename: str,
    paper_ids: Iterable[int],
    expires_at: dt.datetime,
    file_id: str | None = None,
    content_type: str | None = None,
) -> str:
    """Atomically register an output artifact and confirm its quota reservation."""
    artifact_id = uuid.uuid4().hex
    current = utc_now()
    with get_session() as session:
        _lock_quota_write(session, lock_key=f"quota:{actor_value(actor, 'actor_id')}:{local_business_date(current)}:paper")
        session.add(DownloadArtifact(
            artifact_id=artifact_id,
            file_id=file_id,
            actor_id=actor_value(actor, "actor_id"),
            action=action,
            filename=filename,
            content_type=content_type,
            paper_ids_json=_json(_unique_paper_ids(paper_ids)),
            reservation_id=handle.reservation_id,
            expires_at_utc=ensure_utc(expires_at),
        ))

        if handle.enforced and handle.reservation_id and not handle.already_completed:
            reservation = session.query(QuotaReservation).filter_by(
                reservation_id=handle.reservation_id
            ).first()
            if not reservation or reservation.state != "reserved":
                raise RuntimeError("quota reservation is no longer active")
            usage = session.query(QuotaUsage).filter_by(
                actor_id=handle.actor_id,
                business_date=reservation.business_date,
                resource_type="paper",
            ).one()
            changed = session.query(QuotaUsageItem).filter(
                QuotaUsageItem.usage_id == usage.id,
                QuotaUsageItem.reservation_id == handle.reservation_id,
                QuotaUsageItem.state == "reserved",
            ).all()
            for item in changed:
                item.state = "confirmed"
                item.updated_at_utc = current
            count = len(changed)
            usage.reserved_count = max(0, int(usage.reserved_count) - count)
            usage.used_count = int(usage.used_count) + count
            usage.updated_at_utc = current
            reservation.state = "confirmed"
            reservation.artifact_id = artifact_id
            reservation.updated_at_utc = current
    return artifact_id


def record_artifact_access(file_id: str, *, result: str, request_id: str | None = None) -> None:
    with get_session() as session:
        artifact = session.query(DownloadArtifact).filter_by(file_id=file_id).first()
        if not artifact:
            return
        artifact.last_accessed_at_utc = utc_now()
        artifact.access_count = int(artifact.access_count) + 1
        actor = {
            "actor_id": artifact.actor_id,
            "username": "signed-link",
            "role": "artifact-owner",
            "login_source": "signed_link",
        }
        # Commit artifact update before the independent append-only audit write.
    record_audit(
        actor,
        action="paper.artifact_download",
        resource_type="download_artifact",
        resource_id=file_id,
        resource_count=1,
        result=result,
        request_id=request_id,
    )


def serialize_access_request(row: AccessRequest) -> dict[str, Any]:
    return {
        "id": row.id,
        "requester_actor_id": row.requester_actor_id,
        "requester_name": row.requester_name,
        "requester_role": row.requester_role,
        "business_date": row.business_date.isoformat(),
        "request_type": row.request_type,
        "action": row.action,
        "requested_count": row.requested_count,
        "paper_ids": _loads(row.paper_ids_json, []),
        "reason": row.reason,
        "status": row.status,
        "reviewer_actor_id": row.reviewer_actor_id,
        "reviewer_name": row.reviewer_name,
        "review_comment": row.review_comment,
        "approved_count": row.approved_count,
        "expires_at": format_api_datetime(row.expires_at_utc),
        "created_at": format_api_datetime(row.created_at_utc),
        "reviewed_at": format_api_datetime(row.reviewed_at_utc),
        "version": row.version,
    }
