"""Quota, access-request, approval, and audit administration API."""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select

from paper_library.db import get_session
from paper_library.governance import (
    get_quota_snapshot,
    record_audit,
    sanitize_details,
    serialize_access_request,
)
from paper_library.governance_models import (
    AccessRequest,
    AuditEvent,
    QuotaGrant,
    QuotaPolicy,
    QuotaUsage,
)
from paper_library.models import Paper
from paper_library.time_utils import (
    local_business_date,
    local_end_of_day_utc,
    format_api_datetime,
    parse_api_datetime,
    utc_now,
)

from ..dependencies import get_current_user, require_admin


router = APIRouter(tags=["governance"])


def _request_meta(request: Request) -> dict[str, str | None]:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


class AccessRequestCreate(BaseModel):
    request_type: Literal["exact_scope", "daily_extra"] = "exact_scope"
    action: str = Field(min_length=2, max_length=80)
    paper_ids: list[int] = Field(default_factory=list, max_length=5000)
    requested_count: int | None = Field(default=None, ge=1, le=5000)
    reason: str = Field(min_length=2, max_length=1000)

    @field_validator("paper_ids")
    @classmethod
    def normalize_ids(cls, value: list[int]) -> list[int]:
        return list(dict.fromkeys(item for item in value if item > 0))


class AccessRequestReview(BaseModel):
    grant_type: Literal["exact_scope", "daily_extra"] | None = None
    approved_count: int | None = Field(default=None, ge=1, le=5000)
    review_comment: str = Field(default="", max_length=1000)
    expires_at: str | None = None
    version: int | None = Field(default=None, ge=1)


class AccessRequestReject(BaseModel):
    review_comment: str = Field(min_length=1, max_length=1000)
    version: int | None = Field(default=None, ge=1)


class PolicyUpdate(BaseModel):
    limit_value: int = Field(ge=1, le=100000)
    enabled: bool


class AuditArchiveRequest(BaseModel):
    before_date: dt.date
    delete_archived_views: bool = False


@router.get("/api/governance/quota/me")
def my_quota(user: dict = Depends(get_current_user)):
    return get_quota_snapshot(user).as_dict()


@router.post("/api/governance/access-requests")
def create_access_request(
    body: AccessRequestCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if user["role"] != "limited":
        raise HTTPException(status_code=403, detail="仅受配额限制的角色需要提交额度申请")
    if body.request_type == "exact_scope" and not body.paper_ids:
        raise HTTPException(status_code=422, detail="精确范围申请必须包含论文")
    count = len(body.paper_ids) if body.request_type == "exact_scope" else int(body.requested_count or 0)
    if count < 1:
        raise HTTPException(status_code=422, detail="申请数量必须大于 0")

    current_date = local_business_date()
    with get_session() as session:
        duplicate = session.query(AccessRequest).filter(
            AccessRequest.requester_actor_id == user["actor_id"],
            AccessRequest.business_date == current_date,
            AccessRequest.request_type == body.request_type,
            AccessRequest.action == body.action,
            AccessRequest.requested_count == count,
            AccessRequest.paper_ids_json == json.dumps(body.paper_ids, ensure_ascii=False, separators=(",", ":")),
            AccessRequest.status == "pending",
        ).first()
        if duplicate:
            return serialize_access_request(duplicate)
        row = AccessRequest(
            requester_actor_id=user["actor_id"],
            requester_name=user.get("name"),
            requester_role=user["role"],
            business_date=current_date,
            request_type=body.request_type,
            action=body.action,
            requested_count=count,
            paper_ids_json=json.dumps(body.paper_ids, ensure_ascii=False, separators=(",", ":")),
            reason=body.reason.strip(),
            status="pending",
        )
        session.add(row)
        session.flush()
        payload = serialize_access_request(row)
    record_audit(
        user,
        action="governance.request_create",
        resource_type="access_request",
        resource_id=payload["id"],
        resource_count=count,
        result="success",
        details={"request_type": body.request_type, "action": body.action, "paper_ids": body.paper_ids},
        **_request_meta(request),
    )
    return payload


@router.get("/api/governance/access-requests/me")
def my_access_requests(
    status: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    with get_session() as session:
        query = session.query(AccessRequest).filter_by(requester_actor_id=user["actor_id"])
        if status:
            query = query.filter(AccessRequest.status == status)
        rows = query.order_by(AccessRequest.created_at_utc.desc()).limit(200).all()
        return [serialize_access_request(row) for row in rows]


@router.post("/api/governance/access-requests/{request_id}/cancel")
def cancel_access_request(
    request_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    with get_session() as session:
        row = session.query(AccessRequest).filter_by(id=request_id).first()
        if not row or row.requester_actor_id != user["actor_id"]:
            raise HTTPException(status_code=404, detail="申请不存在")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="只有待审批申请可以撤销")
        row.status = "cancelled"
        row.version += 1
        payload = serialize_access_request(row)
    record_audit(
        user,
        action="governance.request_cancel",
        resource_type="access_request",
        resource_id=request_id,
        result="success",
        **_request_meta(request),
    )
    return payload


@router.get("/api/admin/governance/access-requests")
def list_access_requests(
    status: str | None = Query(default=None),
    business_date: dt.date | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_admin),
):
    del user
    with get_session() as session:
        query = session.query(AccessRequest)
        if status:
            query = query.filter(AccessRequest.status == status)
        if business_date:
            query = query.filter(AccessRequest.business_date == business_date)
        total = query.count()
        rows = query.order_by(AccessRequest.created_at_utc.desc()).offset(offset).limit(limit).all()
        return {"total": total, "items": [serialize_access_request(row) for row in rows]}


def _review_row(session, request_id: int, version: int | None) -> AccessRequest:
    if session.bind and session.bind.dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    row = session.query(AccessRequest).filter_by(id=request_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="申请不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="申请已被处理")
    if version is not None and row.version != version:
        raise HTTPException(status_code=409, detail="申请已发生变化，请刷新后重试")
    return row


@router.post("/api/admin/governance/access-requests/{request_id}/approve")
def approve_access_request(
    request_id: int,
    body: AccessRequestReview,
    request: Request,
    user: dict = Depends(require_admin),
):
    current = utc_now()
    with get_session() as session:
        row = _review_row(session, request_id, body.version)
        if row.requester_actor_id == user["actor_id"]:
            raise HTTPException(status_code=409, detail="不能审批自己的申请")
        grant_type = body.grant_type or row.request_type
        approved_count = int(body.approved_count or row.requested_count)
        expires_at = (
            parse_api_datetime(body.expires_at)
            if body.expires_at
            else local_end_of_day_utc(row.business_date)
        )
        if expires_at <= current:
            raise HTTPException(status_code=422, detail="授权有效期必须晚于当前时间")
        paper_ids = json.loads(row.paper_ids_json or "[]")
        if grant_type == "exact_scope" and not paper_ids:
            raise HTTPException(status_code=422, detail="精确范围授权缺少论文")
        if grant_type == "exact_scope" and approved_count != len(paper_ids):
            raise HTTPException(status_code=422, detail="精确范围授权数量必须与论文范围一致")

        grant = QuotaGrant(
            access_request_id=row.id,
            actor_id=row.requester_actor_id,
            business_date=row.business_date,
            grant_type=grant_type,
            action=row.action if grant_type == "exact_scope" else None,
            granted_count=approved_count,
            paper_ids_json=row.paper_ids_json if grant_type == "exact_scope" else "[]",
            expires_at_utc=expires_at,
            created_by_actor_id=user["actor_id"],
        )
        session.add(grant)
        if grant_type == "daily_extra":
            usage = session.query(QuotaUsage).filter_by(
                actor_id=row.requester_actor_id,
                business_date=row.business_date,
                resource_type="paper",
            ).first()
            if usage is None:
                policy = session.query(QuotaPolicy).filter_by(
                    role=row.requester_role, resource_type="paper", period_type="day"
                ).first()
                usage = QuotaUsage(
                    actor_id=row.requester_actor_id,
                    business_date=row.business_date,
                    resource_type="paper",
                    base_limit=int(policy.limit_value) if policy else 100,
                )
                session.add(usage)
            usage.extra_granted = int(usage.extra_granted) + approved_count
            usage.updated_at_utc = current
        row.status = "approved"
        row.reviewer_actor_id = user["actor_id"]
        row.reviewer_name = user.get("name")
        row.review_comment = body.review_comment.strip()
        row.approved_count = approved_count
        row.expires_at_utc = expires_at
        row.reviewed_at_utc = current
        row.version += 1
        session.flush()
        payload = serialize_access_request(row)
    record_audit(
        user,
        action="governance.request_approve",
        resource_type="access_request",
        resource_id=request_id,
        resource_count=approved_count,
        result="success",
        details={"grant_type": grant_type, "expires_at": format_api_datetime(expires_at)},
        **_request_meta(request),
    )
    return payload


@router.post("/api/admin/governance/access-requests/{request_id}/reject")
def reject_access_request(
    request_id: int,
    body: AccessRequestReject,
    request: Request,
    user: dict = Depends(require_admin),
):
    with get_session() as session:
        row = _review_row(session, request_id, body.version)
        if row.requester_actor_id == user["actor_id"]:
            raise HTTPException(status_code=409, detail="不能审批自己的申请")
        row.status = "rejected"
        row.reviewer_actor_id = user["actor_id"]
        row.reviewer_name = user.get("name")
        row.review_comment = body.review_comment.strip()
        row.reviewed_at_utc = utc_now()
        row.version += 1
        payload = serialize_access_request(row)
    record_audit(
        user,
        action="governance.request_reject",
        resource_type="access_request",
        resource_id=request_id,
        result="success",
        details={"comment": body.review_comment},
        **_request_meta(request),
    )
    return payload


@router.get("/api/admin/governance/audit-events")
def list_audit_events(
    date_from: dt.date | None = Query(default=None),
    date_to: dt.date | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    role: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    result: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_admin),
):
    del user
    with get_session() as session:
        query = session.query(AuditEvent)
        if date_from:
            query = query.filter(AuditEvent.business_date >= date_from)
        if date_to:
            query = query.filter(AuditEvent.business_date <= date_to)
        if actor_id:
            query = query.filter(AuditEvent.actor_id == actor_id)
        if role:
            query = query.filter(AuditEvent.role == role)
        if action:
            if action.endswith(".delete"):
                legacy_action = f"{action.removesuffix('.delete')}.change"
                query = query.filter(or_(
                    AuditEvent.action == action,
                    and_(
                        AuditEvent.action == legacy_action,
                        AuditEvent.details_json.like('%"method":"DELETE"%'),
                    ),
                ))
            else:
                query = query.filter(AuditEvent.action == action)
        if resource_type:
            query = query.filter(AuditEvent.resource_type == resource_type)
        if result:
            query = query.filter(AuditEvent.result == result)
        total = query.count()
        rows = query.order_by(AuditEvent.occurred_at_utc.desc()).offset(offset).limit(limit).all()
        details_by_event_id = {
            row.event_id: sanitize_details(json.loads(row.details_json or "{}"))
            for row in rows
        }
        paper_db_ids: set[int] = set()
        for row in rows:
            if row.resource_type != "paper":
                continue
            details = details_by_event_id[row.event_id]
            stored_ids = details.get("paper_ids", [])
            if isinstance(stored_ids, list):
                paper_db_ids.update(value for value in stored_ids if isinstance(value, int))
            if not stored_ids and (row.resource_id or "").isdigit():
                paper_db_ids.add(int(row.resource_id))
        paper_internal_ids: dict[int, int] = {}
        if paper_db_ids:
            try:
                with get_session() as paper_session:
                    paper_internal_ids = dict(paper_session.execute(
                        select(Paper.id, Paper.internal_id).where(
                            Paper.id.in_(paper_db_ids),
                            Paper.internal_id.is_not(None),
                        )
                    ).all())
            except Exception:
                # Audit history must remain readable if the papers DB is
                # temporarily unavailable.
                paper_internal_ids = {}

        def serialize_row(row: AuditEvent) -> dict:
            details = details_by_event_id[row.event_id]
            stored_paper_ids = details.get("paper_ids", [])
            if isinstance(stored_paper_ids, list):
                row_paper_ids = [value for value in stored_paper_ids if isinstance(value, int)]
            else:
                row_paper_ids = []
            if not row_paper_ids and row.resource_type == "paper" and (row.resource_id or "").isdigit():
                row_paper_ids = [int(row.resource_id)]
            cite_ids = details.get("paper_internal_ids", [])
            if not isinstance(cite_ids, list):
                cite_ids = []
            cite_ids = [value for value in cite_ids if isinstance(value, int)]
            if not cite_ids:
                cite_ids = [
                    paper_internal_ids[paper_id]
                    for paper_id in row_paper_ids
                    if paper_id in paper_internal_ids
                ]
            return {
                "event_id": row.event_id,
                "occurred_at": format_api_datetime(row.occurred_at_utc),
                "business_date": row.business_date.isoformat(),
                "actor_id": row.actor_id,
                "username": row.username,
                "role": row.role,
                "display_name": row.display_name,
                "department": row.department,
                "login_source": row.login_source,
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "resource_count": row.resource_count,
                "result": row.result,
                "request_id": row.request_id,
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "paper_ids": row_paper_ids,
                "paper_internal_ids": cite_ids,
                "details": details,
            }
        return {
            "total": total,
            "items": [serialize_row(row) for row in rows],
        }


@router.get("/api/admin/governance/quota-usage")
def list_quota_usage(
    business_date: dt.date | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    user: dict = Depends(require_admin),
):
    del user
    with get_session() as session:
        query = session.query(QuotaUsage)
        if business_date:
            query = query.filter(QuotaUsage.business_date == business_date)
        if actor_id:
            query = query.filter(QuotaUsage.actor_id == actor_id)
        rows = query.order_by(QuotaUsage.business_date.desc(), QuotaUsage.used_count.desc()).limit(limit).all()
        return [{
            "actor_id": row.actor_id,
            "business_date": row.business_date.isoformat(),
            "base_limit": row.base_limit,
            "extra_granted": row.extra_granted,
            "used": row.used_count,
            "reserved": row.reserved_count,
            "updated_at": format_api_datetime(row.updated_at_utc),
        } for row in rows]


@router.get("/api/admin/governance/quota-policies")
def list_quota_policies(user: dict = Depends(require_admin)):
    del user
    with get_session() as session:
        rows = session.query(QuotaPolicy).order_by(QuotaPolicy.role).all()
        return [{
            "id": row.id,
            "role": row.role,
            "resource_type": row.resource_type,
            "period_type": row.period_type,
            "limit_value": row.limit_value,
            "timezone": row.timezone,
            "enabled": row.enabled,
            "updated_at": format_api_datetime(row.updated_at_utc),
        } for row in rows]


@router.put("/api/admin/governance/quota-policies/{policy_id}")
def update_quota_policy(
    policy_id: int,
    body: PolicyUpdate,
    request: Request,
    user: dict = Depends(require_admin),
):
    with get_session() as session:
        row = session.query(QuotaPolicy).filter_by(id=policy_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="额度策略不存在")
        before = {"limit_value": row.limit_value, "enabled": row.enabled}
        row.limit_value = body.limit_value
        row.enabled = body.enabled
        row.updated_at_utc = utc_now()
        payload = {
            "id": row.id,
            "role": row.role,
            "limit_value": row.limit_value,
            "enabled": row.enabled,
            "timezone": row.timezone,
        }
    record_audit(
        user,
        action="governance.policy_update",
        resource_type="quota_policy",
        resource_id=policy_id,
        result="success",
        details={"before": before, "after": payload},
        **_request_meta(request),
    )
    return payload


@router.post("/api/admin/governance/audit-events/archive")
def archive_audit(
    body: AuditArchiveRequest,
    request: Request,
    user: dict = Depends(require_admin),
):
    if body.before_date >= local_business_date():
        raise HTTPException(status_code=422, detail="归档截止日期必须早于今天（北京时间）")
    from paper_library.governance_maintenance import archive_audit_events

    result = archive_audit_events(
        body.before_date,
        delete_archived_views=body.delete_archived_views,
    )
    record_audit(
        user,
        action="governance.audit_archive",
        resource_type="audit_archive",
        resource_count=result["archived"],
        result="success",
        details={"before_date": body.before_date.isoformat(), "deleted_online": result["deleted_online"]},
        **_request_meta(request),
    )
    return result
