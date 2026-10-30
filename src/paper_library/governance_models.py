"""Persistent account-governance models."""

from __future__ import annotations


from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from .models import Base

from .time_utils import BUSINESS_TIMEZONE, utc_now


# Kept as an import-compatible alias; governance tables share Base.metadata.
GovernanceBase = Base


class QuotaPolicy(Base):
    __tablename__ = "quota_policies"
    __table_args__ = (
        UniqueConstraint("role", "resource_type", "period_type", name="uq_quota_policy_scope"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(20), nullable=False, index=True)
    resource_type = Column(String(30), nullable=False, default="paper")
    period_type = Column(String(20), nullable=False, default="day")
    limit_value = Column(Integer, nullable=False, default=100)
    timezone = Column(String(50), nullable=False, default=BUSINESS_TIMEZONE)
    enabled = Column(Boolean, nullable=False, default=False)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class QuotaUsage(Base):
    __tablename__ = "quota_usage"
    __table_args__ = (
        UniqueConstraint("actor_id", "business_date", "resource_type", name="uq_quota_usage_day"),
        Index("ix_quota_usage_date_actor", "business_date", "actor_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_id = Column(String(180), nullable=False, index=True)
    business_date = Column(Date, nullable=False, index=True)
    resource_type = Column(String(30), nullable=False, default="paper")
    base_limit = Column(Integer, nullable=False, default=100)
    extra_granted = Column(Integer, nullable=False, default=0)
    used_count = Column(Integer, nullable=False, default=0)
    reserved_count = Column(Integer, nullable=False, default=0)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class QuotaUsageItem(Base):
    __tablename__ = "quota_usage_items"
    __table_args__ = (
        UniqueConstraint("usage_id", "paper_id", name="uq_quota_usage_paper"),
        Index("ix_quota_item_reservation", "reservation_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    usage_id = Column(Integer, ForeignKey("quota_usage.id", ondelete="CASCADE"), nullable=False)
    paper_id = Column(Integer, nullable=False, index=True)
    state = Column(String(20), nullable=False, default="reserved")
    reservation_id = Column(String(64), nullable=True)
    first_action = Column(String(80), nullable=False)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class QuotaReservation(Base):
    __tablename__ = "quota_reservations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reservation_id = Column(String(64), nullable=False, unique=True, index=True)
    actor_id = Column(String(180), nullable=False, index=True)
    business_date = Column(Date, nullable=False, index=True)
    action = Column(String(80), nullable=False)
    state = Column(String(20), nullable=False, default="reserved")
    paper_ids_json = Column(Text, nullable=False)
    new_paper_ids_json = Column(Text, nullable=False)
    artifact_id = Column(String(64), nullable=True, index=True)
    error_code = Column(String(80), nullable=True)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AccessRequest(Base):
    __tablename__ = "access_requests"
    __table_args__ = (Index("ix_access_request_status_date", "status", "business_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    requester_actor_id = Column(String(180), nullable=False, index=True)
    requester_name = Column(String(120), nullable=True)
    requester_role = Column(String(20), nullable=False)
    business_date = Column(Date, nullable=False, index=True)
    request_type = Column(String(30), nullable=False, default="exact_scope")
    action = Column(String(80), nullable=False)
    requested_count = Column(Integer, nullable=False)
    paper_ids_json = Column(Text, nullable=False, default="[]")
    reason = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    reviewer_actor_id = Column(String(180), nullable=True)
    reviewer_name = Column(String(120), nullable=True)
    review_comment = Column(Text, nullable=True)
    approved_count = Column(Integer, nullable=True)
    expires_at_utc = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    reviewed_at_utc = Column(DateTime(timezone=True), nullable=True)


class QuotaGrant(Base):
    __tablename__ = "quota_grants"
    __table_args__ = (Index("ix_quota_grant_actor_date", "actor_id", "business_date"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    access_request_id = Column(Integer, ForeignKey("access_requests.id"), nullable=False, index=True)
    actor_id = Column(String(180), nullable=False, index=True)
    business_date = Column(Date, nullable=False, index=True)
    grant_type = Column(String(30), nullable=False)
    action = Column(String(80), nullable=True)
    granted_count = Column(Integer, nullable=False, default=0)
    paper_ids_json = Column(Text, nullable=False, default="[]")
    expires_at_utc = Column(DateTime(timezone=True), nullable=False)
    created_by_actor_id = Column(String(180), nullable=False)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_event_date_action", "business_date", "action"),
        Index("ix_audit_event_actor_time", "actor_id", "occurred_at_utc"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, unique=True, index=True)
    occurred_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    business_date = Column(Date, nullable=False, index=True)
    actor_id = Column(String(180), nullable=False, index=True)
    username = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False)
    display_name = Column(String(120), nullable=True)
    department = Column(String(200), nullable=True)
    login_source = Column(String(20), nullable=False)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(180), nullable=True)
    resource_count = Column(Integer, nullable=False, default=0)
    result = Column(String(20), nullable=False, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(500), nullable=True)
    details_json = Column(Text, nullable=False, default="{}")


class DownloadArtifact(Base):
    __tablename__ = "download_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(64), nullable=False, unique=True, index=True)
    file_id = Column(String(64), nullable=True, unique=True, index=True)
    actor_id = Column(String(180), nullable=False, index=True)
    action = Column(String(80), nullable=False)
    filename = Column(String(300), nullable=False)
    content_type = Column(String(120), nullable=True)
    paper_ids_json = Column(Text, nullable=False, default="[]")
    reservation_id = Column(String(64), nullable=True, index=True)
    expires_at_utc = Column(DateTime(timezone=True), nullable=False)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_accessed_at_utc = Column(DateTime(timezone=True), nullable=True)
    access_count = Column(Integer, nullable=False, default=0)


class SummaryJob(Base):
    """Persistent Word-summary job and its resumable intermediate result."""

    __tablename__ = "summary_jobs"
    __table_args__ = (
        Index("ix_summary_job_actor_created", "actor_id", "created_at_utc"),
        Index("ix_summary_job_fingerprint_status", "fingerprint", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), nullable=False, unique=True, index=True)
    fingerprint = Column(String(64), nullable=False, index=True)
    actor_id = Column(String(180), nullable=False, index=True)
    actor_json = Column(Text, nullable=False)
    paper_ids_json = Column(Text, nullable=False)
    profile = Column(String(50), nullable=False, default="report")
    model = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    progress = Column(Integer, nullable=False, default=0)
    stage = Column(String(80), nullable=False, default="queued")
    attempt_count = Column(Integer, nullable=False, default=0)
    repair_count = Column(Integer, nullable=False, default=0)
    report_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at_utc = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    expires_at_utc = Column(DateTime(timezone=True), nullable=True)
