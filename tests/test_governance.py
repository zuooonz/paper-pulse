import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from paper_library import db
from paper_library.governance import (
    QuotaExceeded,
    confirm_reservation,
    expire_access_requests,
    get_quota_snapshot,
    record_audit,
    release_reservation,
    reserve_papers,
)
from paper_library.governance_models import AuditEvent, DownloadArtifact, GovernanceBase, QuotaPolicy, QuotaReservation
from paper_library.time_utils import beijing_business_date, format_api_datetime
from paper_library.governance_maintenance import archive_audit_events, backup_governance_database
from api.dependencies import get_current_user
from api.dependencies import _decode_token, create_token
from api.routers.governance import router as governance_router
from api.governance_integration import GovernedPaperOutput
from api.scheduler import _make_aware
from api.services.publishing import parse_scheduled_datetime


def actor(actor_id="dingtalk:limited-a"):
    return {
        "actor_id": actor_id,
        "username": "limited",
        "role": "limited",
        "name": "受限用户",
        "department": "受限组",
        "login_source": "dingtalk",
    }


def admin_actor():
    return {
        "actor_id": "local:admin",
        "username": "admin",
        "role": "admin",
        "name": "管理员",
        "department": "",
        "login_source": "local",
    }


def fake_request() -> Request:
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/papers/export",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
    })
    request.state.request_id = "request-test"
    return request


@pytest.fixture()
def governance_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    db.reset_engines()
    engine = db.get_engine()
    GovernanceBase.metadata.create_all(engine)
    with db.get_session() as session:
        session.add(QuotaPolicy(
            role="limited",
            resource_type="paper",
            period_type="day",
            limit_value=100,
            timezone="Asia/Shanghai",
            enabled=True,
        ))
    yield
    db.reset_engines()


def _add_dingtalk_mapping(dingtalk_userid: str, internal_username: str, role: str) -> None:
    from paper_library.db import get_session
    from paper_library.models import DingTalkUser

    with get_session() as session:
        if session.query(DingTalkUser).filter_by(dingtalk_userid=dingtalk_userid).first() is None:
            session.add(DingTalkUser(
                dingtalk_userid=dingtalk_userid,
                internal_username=internal_username,
                role=role,
                display_name=internal_username,
            ))
            session.commit()


def test_beijing_business_day_boundary():
    assert beijing_business_date(dt.datetime(2026, 7, 20, 15, 59, tzinfo=dt.UTC)).isoformat() == "2026-07-20"
    assert beijing_business_date(dt.datetime(2026, 7, 20, 16, 0, tzinfo=dt.UTC)).isoformat() == "2026-07-21"
    assert format_api_datetime(dt.datetime(2026, 7, 20, 7, 30, tzinfo=dt.UTC)) == "2026-07-20T15:30:00+08:00"
    assert parse_scheduled_datetime("2026-07-20T10:00") == dt.datetime(2026, 7, 20, 2, 0, tzinfo=dt.UTC)
    assert _make_aware(dt.datetime(2026, 7, 20, 2, 0)) == dt.datetime(2026, 7, 20, 2, 0, tzinfo=dt.UTC)


def test_limit_repeat_and_atomic_rejection(governance_db):
    first = reserve_papers(actor(), range(1, 101), action="paper.export_excel")
    confirm_reservation(first)
    snapshot = get_quota_snapshot(actor())
    assert snapshot.used == 100
    assert snapshot.remaining == 0

    repeat = reserve_papers(actor(), [1, 2, 3], action="paper.export_excel")
    assert repeat.new_paper_ids == ()
    confirm_reservation(repeat)
    assert get_quota_snapshot(actor()).used == 100

    with pytest.raises(QuotaExceeded) as caught:
        reserve_papers(actor(), [100, 101], action="paper.export_excel")
    assert caught.value.requested_new == 1
    assert get_quota_snapshot(actor()).used == 100


def test_failure_releases_and_retry_is_idempotent(governance_db):
    handle = reserve_papers(actor(), [1, 2], action="paper.batch_pdf", reservation_id="retry-key")
    assert get_quota_snapshot(actor()).reserved == 2
    same = reserve_papers(actor(), [1, 2], action="paper.batch_pdf", reservation_id="retry-key")
    assert same.reservation_id == handle.reservation_id
    assert get_quota_snapshot(actor()).reserved == 2
    release_reservation(handle, error_code="test_failure")
    assert get_quota_snapshot(actor()).reserved == 0
    assert get_quota_snapshot(actor()).used == 0


def test_people_mapped_to_same_role_are_isolated(governance_db):
    a = reserve_papers(actor("dingtalk:a"), range(1, 11), action="paper.export_excel")
    b = reserve_papers(actor("dingtalk:b"), range(1, 6), action="paper.export_excel")
    confirm_reservation(a)
    confirm_reservation(b)
    assert get_quota_snapshot(actor("dingtalk:a")).used == 10
    assert get_quota_snapshot(actor("dingtalk:b")).used == 5


def test_token_carries_stable_actor_identity():
    _add_dingtalk_mapping("user-a", "limited", "limited")
    token = create_token(
        "limited",
        role="limited",
        name="受限用户",
        actor_id="dingtalk:user-a",
        login_source="dingtalk",
        department="受限组",
    )
    decoded = _decode_token(token)
    assert decoded["actor_id"] == "dingtalk:user-a"
    assert decoded["login_source"] == "dingtalk"
    assert decoded["department"] == "受限组"
    assert _decode_token(create_token("limited"))["actor_id"] == "local:limited"


def test_concurrent_requests_cannot_oversubscribe(governance_db):
    with db.get_session() as session:
        policy = session.query(QuotaPolicy).one()
        policy.limit_value = 1

    def attempt(paper_id):
        try:
            handle = reserve_papers(actor(), [paper_id], action="paper.export_excel")
            confirm_reservation(handle)
            return "ok"
        except QuotaExceeded:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [1, 2]))
    assert sorted(results) == ["denied", "ok"]
    assert get_quota_snapshot(actor()).used == 1


def test_audit_redacts_secrets(governance_db):
    record_audit(
        actor(),
        action="auth.test",
        resource_type="session",
        result="success",
        details={"token": "secret-token", "nested": {"api_key": "secret-key", "safe": "ok"}},
    )
    with db.get_session() as session:
        row = session.query(AuditEvent).one()
        assert "secret-token" not in row.details_json
        assert "secret-key" not in row.details_json
        assert "[redacted]" in row.details_json


def test_output_integration_confirms_artifact_and_audit(governance_db, monkeypatch):
    monkeypatch.setattr(
        "api.governance_integration.paper_identity_snapshot",
        lambda paper_ids: {
            "paper_ids": list(paper_ids),
            "paper_internal_ids": [1010, 1011],
        },
    )
    operation = GovernedPaperOutput(actor(), [10, 11], action="paper.export_excel", request=fake_request())
    artifact_id = operation.complete({
        "file_id": "file-test",
        "filename": "papers.xlsx",
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "expires_at": int(dt.datetime.now(dt.UTC).timestamp()) + 3600,
    })
    assert get_quota_snapshot(actor()).used == 2
    with db.get_session() as session:
        actions = {row.action for row in session.query(AuditEvent).all()}
        assert session.query(DownloadArtifact).filter_by(file_id="file-test").one()
        assert session.query(QuotaReservation).one().state == "confirmed"
        output_event = session.query(AuditEvent).filter_by(action="paper.export_excel").one()
        output_details = json.loads(output_event.details_json)
        assert output_event.resource_id is None
        assert output_details["artifact_id"] == artifact_id
        assert output_details["paper_ids"] == [10, 11]
        assert output_details["paper_internal_ids"] == [1010, 1011]
    assert {"governance.quota_reserve", "governance.quota_confirm", "paper.export_excel"} <= actions


def test_output_integration_releases_on_failure(governance_db):
    operation = GovernedPaperOutput(actor(), [20], action="paper.batch_pdf", request=fake_request())
    operation.fail(RuntimeError("zip failed"))
    snapshot = get_quota_snapshot(actor())
    assert snapshot.used == 0
    assert snapshot.reserved == 0


def test_access_request_approval_and_exact_grant(governance_db):
    app = FastAPI()
    app.include_router(governance_router)
    app.dependency_overrides[get_current_user] = actor
    client = TestClient(app)

    created = client.post("/api/governance/access-requests", json={
        "request_type": "exact_scope",
        "action": "paper.export_excel",
        "paper_ids": [2],
        "reason": "客户项目需要补充一篇论文",
    })
    assert created.status_code == 200
    request_row = created.json()

    app.dependency_overrides[get_current_user] = admin_actor
    approved = client.post(
        f"/api/admin/governance/access-requests/{request_row['id']}/approve",
        json={"version": request_row["version"], "review_comment": "同意本次范围"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["expires_at"].endswith("+08:00")
    duplicate_approval = client.post(
        f"/api/admin/governance/access-requests/{request_row['id']}/approve",
        json={"version": request_row["version"], "review_comment": "重复审批"},
    )
    assert duplicate_approval.status_code == 409

    app.dependency_overrides[get_current_user] = actor
    with db.get_session() as session:
        session.query(QuotaPolicy).one().limit_value = 1
    first = reserve_papers(actor(), [1], action="paper.export_excel")
    confirm_reservation(first)
    granted = reserve_papers(actor(), [2], action="paper.export_excel")
    assert granted.new_paper_ids == ()
    with pytest.raises(QuotaExceeded):
        reserve_papers(actor(), [2], action="paper.batch_pdf")


def test_non_admin_cannot_read_audit(governance_db):
    app = FastAPI()
    app.include_router(governance_router)
    app.dependency_overrides[get_current_user] = actor
    response = TestClient(app).get("/api/admin/governance/audit-events")
    assert response.status_code == 403


def test_audit_api_enriches_current_and_deleted_papers_with_cite_ids(governance_db, monkeypatch):
    record_audit(
        admin_actor(), action="paper.view", resource_type="paper",
        resource_id=3006, result="success",
    )
    record_audit(
        admin_actor(), action="paper.delete", resource_type="paper",
        resource_id=7008, result="success",
        details={"paper_internal_ids": [753]},
    )
    record_audit(
        admin_actor(), action="paper.get_pdf", resource_type="paper",
        resource_id="legacy-artifact-id", result="success",
        details={"paper_ids": [3006], "artifact_id": "legacy-artifact-id"},
    )

    from paper_library.models import Base as PaperBase, Paper

    PaperBase.metadata.create_all(db.get_engine())
    with db.get_session() as session:
        session.add(Paper(id=3006, internal_id=656, title="Synthetic audit paper"))

    app = FastAPI()
    app.include_router(governance_router)
    app.dependency_overrides[get_current_user] = admin_actor
    response = TestClient(app).get("/api/admin/governance/audit-events")

    assert response.status_code == 200
    by_resource = {row["resource_id"]: row for row in response.json()["items"]}
    assert by_resource["3006"]["paper_internal_ids"] == [656]
    assert by_resource["3006"]["paper_ids"] == [3006]
    assert by_resource["7008"]["paper_internal_ids"] == [753]
    assert by_resource["legacy-artifact-id"]["paper_ids"] == [3006]
    assert by_resource["legacy-artifact-id"]["paper_internal_ids"] == [656]


def test_delete_filter_includes_legacy_delete_events_but_not_regular_changes(governance_db):
    record_audit(
        admin_actor(), action="paper.change", resource_type="paper",
        resource_id=3006, result="success", details={"method": "DELETE"},
    )
    record_audit(
        admin_actor(), action="paper.change", resource_type="paper",
        resource_id=3007, result="success", details={"method": "PATCH"},
    )
    record_audit(
        admin_actor(), action="paper.delete", resource_type="paper",
        resource_id=3008, result="success", details={"method": "DELETE"},
    )

    app = FastAPI()
    app.include_router(governance_router)
    app.dependency_overrides[get_current_user] = admin_actor
    response = TestClient(app).get(
        "/api/admin/governance/audit-events",
        params={"action": "paper.delete"},
    )

    assert response.status_code == 200
    assert {row["resource_id"] for row in response.json()["items"]} == {"3006", "3008"}


def test_backup_and_archive_preserve_critical_events(governance_db, tmp_path):
    old_time = dt.datetime(2025, 1, 1, 2, 0, tzinfo=dt.UTC)
    record_audit(actor(), action="paper.view", resource_type="paper", resource_id=1, result="success", occurred_at=old_time)
    record_audit(actor(), action="governance.request_approve", resource_type="access_request", resource_id=2, result="success", occurred_at=old_time)

    backup = backup_governance_database(tmp_path / "backups")
    assert backup.exists() and backup.stat().st_size > 0
    import sqlite3
    with sqlite3.connect(backup) as backup_connection:
        backup_tables = {
            row[0] for row in backup_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"papers", "channel_accounts", "article_drafts", "audit_events"} <= backup_tables
    result = archive_audit_events(
        dt.date(2026, 1, 1),
        archive_dir=tmp_path / "archive",
        delete_archived_views=True,
    )
    assert result["archived"] == 2
    assert result["deleted_online"] == 1
    assert dt.date(2026, 1, 1).isoformat() in result["path"]
    with db.get_session() as session:
        remaining = [row.action for row in session.query(AuditEvent).all()]
    assert remaining == ["governance.request_approve"]


def test_expired_approval_is_marked_but_no_longer_grants_access(governance_db):
    now = dt.datetime.now(dt.UTC)
    with db.get_session() as session:
        from paper_library.governance_models import AccessRequest, QuotaGrant

        request_row = AccessRequest(
            requester_actor_id=actor()["actor_id"],
            requester_name=actor()["name"],
            requester_role="limited",
            business_date=beijing_business_date(now),
            request_type="exact_scope",
            action="paper.export_excel",
            requested_count=1,
            paper_ids_json="[2]",
            reason="test",
            status="approved",
            approved_count=1,
            expires_at_utc=now - dt.timedelta(seconds=1),
        )
        session.add(request_row)
        session.flush()
        session.add(QuotaGrant(
            access_request_id=request_row.id,
            actor_id=actor()["actor_id"],
            business_date=request_row.business_date,
            grant_type="exact_scope",
            action="paper.export_excel",
            granted_count=1,
            paper_ids_json="[2]",
            expires_at_utc=now - dt.timedelta(seconds=1),
            created_by_actor_id="local:admin",
        ))

    assert expire_access_requests(now=now) == 1
    with db.get_session() as session:
        from paper_library.governance_models import AccessRequest

        assert session.query(AccessRequest).one().status == "expired"
    with db.get_session() as session:
        session.query(QuotaPolicy).one().limit_value = 1
    first = reserve_papers(actor(), [1], action="paper.export_excel", now=now)
    confirm_reservation(first)
    with pytest.raises(QuotaExceeded):
        reserve_papers(actor(), [2], action="paper.export_excel", now=now)
