"""Regression tests for the public-release security hardening."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.dependencies import _decode_token, create_token
from api.login_limiter import LoginRateLimiter, LoginRateLimited
from api.main import app
from api.routers.papers.common import can_view_paper, ensure_paper_visible
from paper_library.models import Paper


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_china_provinces_include_taiwan_and_all_34_divisions():
    from paper_library.china_regions import CHINA_PROVINCES

    assert len(CHINA_PROVINCES) == 34
    assert "台湾" in CHINA_PROVINCES
    assert len(set(CHINA_PROVINCES)) == 34


def test_paper_visibility_rules():
    approved = Paper(review_outcome="approved")
    excluded = Paper(review_outcome="excluded")
    pending = Paper(review_outcome="unreviewed")

    assert can_view_paper(approved, {"role": "viewer"})
    assert can_view_paper(approved, {"role": "limited"})
    assert not can_view_paper(excluded, {"role": "viewer"})
    assert can_view_paper(excluded, {"role": "reviewer"})
    assert not can_view_paper(pending, {"role": "limited"})

    for paper in (excluded, pending):
        try:
            ensure_paper_visible(paper, {"role": "limited"})
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("hidden paper must not be visible")


def test_login_limiter_locks_and_recovers():
    clock = _Clock()
    limiter = LoginRateLimiter(max_failures=2, base_lock_seconds=10, max_lock_seconds=20, clock=clock)
    keys = ("ip:1.2.3.4", "user:admin")

    for _ in range(2):
        limiter.record_failure(keys)
    try:
        limiter.check(keys)
    except LoginRateLimited as exc:
        assert exc.retry_after == 10
    else:
        raise AssertionError("limiter should be locked")

    clock.now += 10
    limiter.check(keys)
    limiter.reset(keys)
    limiter.check(keys)


def test_health_and_security_headers():
    with TestClient(app) as client:
        response = client.get("/healthz")
        # A TestClient without an explicit DB override still points at the
        # isolated test database created by conftest.
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_query_access_token_is_not_accepted():
    token = create_token("admin")
    with TestClient(app) as client:
        response = client.get("/api/drafts/images/example.png", params={"access_token": token})
        assert response.status_code in {401, 403}
        response = client.get(
            "/api/drafts/images/example.png",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Authentication succeeds; the missing image itself is a separate 4xx.
        assert response.status_code in {400, 404}


def test_citation_hides_excluded_paper_from_viewer():
    from paper_library.db import get_session

    with get_session() as session:
        paper = Paper(
            title="Excluded citation test",
            processing_status="done",
            review_outcome="excluded",
        )
        session.add(paper)
        session.commit()
        paper_id = paper.id
    token = create_token("reader")
    with TestClient(app) as client:
        response = client.get(
            f"/api/papers/{paper_id}/citation",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


def test_video_file_requires_authentication():
    with TestClient(app) as client:
        response = client.get("/api/videos/file/1")
        assert response.status_code in {401, 403}


def test_settings_derives_local_users_from_password_env(monkeypatch):
    from api.config import Settings

    monkeypatch.delenv("LOCAL_USERS", raising=False)
    monkeypatch.setenv("LOCAL_ADMIN_PASSWORD", "pbkdf2_sha256$600000$abc$def")
    configured = Settings()
    assert configured.local_users["admin"]["password"].startswith("pbkdf2_sha256$")
    assert configured.local_users["admin"]["role"] == "admin"


def test_runtime_yaml_uses_workspace_env(monkeypatch, tmp_path):
    from api.config import Settings

    monkeypatch.delenv("LOCAL_USERS", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "runtime.yaml").write_text(
        "auth:\n  jwt_secret: env-workspace-secret-value-123456\n  local_users:\n    admin:\n      password: secret\n      name: Admin\n      role: admin\n",
        encoding="utf-8",
    )
    configured = Settings()
    assert configured.jwt_secret == "env-workspace-secret-value-123456"
    assert set(configured.local_users) == {"admin"}


def test_jwt_version_rotation_invalidates_old_tokens(monkeypatch):
    from api.config import settings
    from api.dependencies import _decode_token, create_token

    token = create_token("admin")
    assert _decode_token(token)["role"] == "admin"
    monkeypatch.setattr(settings, "jwt_token_version", 2)
    try:
        _decode_token(token)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("old token version must be rejected")


def test_dingtalk_role_change_invalidates_existing_token():
    from paper_library.db import get_session
    from paper_library.models import DingTalkUser

    token = create_token(
        "dingtalk-user",
        role="limited",
        actor_id="dingtalk:security-test",
        login_source="dingtalk",
    )
    with get_session() as session:
        session.add(DingTalkUser(
            dingtalk_userid="security-test",
            internal_username="dingtalk-user",
            role="limited",
            display_name="DingTalk User",
        ))
        session.commit()
    assert _decode_token(token)["role"] == "limited"

    with get_session() as session:
        mapping = session.query(DingTalkUser).filter_by(dingtalk_userid="security-test").one()
        mapping.role = "admin"
        session.commit()
    try:
        _decode_token(token)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("stale DingTalk token must be rejected")
