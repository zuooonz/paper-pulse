"""Unit tests for Playwright auto-login module, cookie validator, and API router."""
from __future__ import annotations
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest
from fastapi.testclient import TestClient

from tools.playwright_auto_login import (
    create_task,
    get_task,
    cleanup_old_tasks,
    LoginMode,
    TaskStatus,
    PLATFORM_CONFIG,
)
from tools.cookie_validator import (
    ValidationStatus,
    compute_health_status,
    validate_cookie,
)
from api.main import app


# ──────────────────────────────────────────────────────────────
# Task management tests
# ──────────────────────────────────────────────────────────────

class TestTaskManagement:
    def test_create_and_get_task(self):
        task = create_task("zhihu", LoginMode.QRCODE, "test_account")
        assert task.channel == "zhihu"
        assert task.mode == LoginMode.QRCODE
        assert task.account_name == "test_account"
        assert task.status == TaskStatus.PENDING

        retrieved = get_task(task.task_id)
        assert retrieved is not None
        assert retrieved.task_id == task.task_id

    def test_get_nonexistent_task(self):
        assert get_task("non-existent-id") is None

    def test_task_cancel(self):
        task = create_task("csdn", LoginMode.SMS, "test")
        assert not task.is_cancelled()
        task.cancel()
        assert task.is_cancelled()
        assert task.status == TaskStatus.CANCELLED

    def test_cleanup_old_tasks(self):
        import time
        task = create_task("sohu", LoginMode.QRCODE, "old")
        task.created_at = time.time() - 7200
        cleanup_old_tasks(max_age_seconds=3600)
        assert get_task(task.task_id) is None

    def test_task_to_dict(self):
        task = create_task("baijiahao", LoginMode.QRCODE, "test")
        d = task.to_dict()
        assert d["channel"] == "baijiahao"
        assert d["mode"] == "qrcode"
        assert "task_id" in d
        assert "cookie_str" in d

    def test_platform_config_coverage(self):
        expected = {"zhihu", "csdn", "xiaohongshu", "baijiahao", "sohu", "bilibili"}
        assert set(PLATFORM_CONFIG.keys()) == expected
        for ch, cfg in PLATFORM_CONFIG.items():
            assert cfg.name
            assert cfg.login_url
            assert cfg.cookie_domain


# ──────────────────────────────────────────────────────────────
# Cookie validator tests
# ──────────────────────────────────────────────────────────────

class TestCookieValidator:
    def test_compute_health_status_healthy(self):
        health = compute_health_status({
            "expires_at": "2026-12-31T00:00:00+00:00",
            "validation_status": "valid",
            "last_validated_at": "2026-04-01T00:00:00+00:00",
        })
        assert health["overall"] == "healthy"
        assert health["ttl_based"] == "healthy"

    def test_compute_health_status_expired_by_ttl(self):
        health = compute_health_status({
            "expires_at": "2020-01-01T00:00:00+00:00",
            "validation_status": "unknown",
        })
        assert health["overall"] == "expired"
        assert health["ttl_based"] == "expired"

    def test_compute_health_status_warning(self):
        import datetime
        future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=12)).isoformat()
        health = compute_health_status({
            "expires_at": future,
            "validation_status": "valid",
        })
        assert health["overall"] == "critical"
        assert health["ttl_based"] == "critical"

    def test_compute_health_status_invalid_overrides_ttl(self):
        health = compute_health_status({
            "expires_at": "2026-12-31T00:00:00+00:00",
            "validation_status": "invalid",
        })
        assert health["overall"] == "expired"

    def test_validate_cookie_unsupported_channel(self):
        result = validate_cookie("unsupported", "test_cookie")
        assert result.status == ValidationStatus.ERROR

    def test_validate_cookie_empty(self):
        result = validate_cookie("zhihu", "")
        assert result.status == ValidationStatus.INVALID


# ──────────────────────────────────────────────────────────────
# API router tests
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def api_client():
    return TestClient(app)


@pytest.fixture
def auth_headers(api_client):
    from api.dependencies import USERS

    resp = api_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": USERS["admin"]["password"]},
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestAutoLoginAPI:
    def test_invalid_channel(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/auto-login", json={
            "channel": "invalid",
            "mode": "qrcode",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_sms_mode_missing_phone(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/auto-login", json={
            "channel": "zhihu",
            "mode": "sms",
            "phone": "",
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_get_nonexistent_task(self, api_client, auth_headers):
        resp = api_client.get("/api/accounts/auto-login/non-existent", headers=auth_headers)
        assert resp.status_code == 404

    def test_cancel_nonexistent_task(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/auto-login/non-existent/cancel", headers=auth_headers)
        assert resp.status_code == 404

    def test_save_before_success(self, api_client, auth_headers):
        from tools.playwright_auto_login import create_task, LoginMode, TaskStatus
        task = create_task("zhihu", LoginMode.QRCODE, "test")
        task.status = TaskStatus.WAITING_FOR_SCAN
        resp = api_client.post(f"/api/accounts/auto-login/{task.task_id}/save", json={"account_name": ""}, headers=auth_headers)
        assert resp.status_code == 400
        task.cancel()

    def test_start_qrcode_flow(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/auto-login", json={
            "channel": "zhihu",
            "mode": "qrcode",
            "account_name": "api_test_zhihu",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        api_client.post(f"/api/accounts/auto-login/{data['task_id']}/cancel", headers=auth_headers)


class TestAccountValidationAPI:
    def test_validate_all_background(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/validate-all", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_list_accounts_has_health(self, api_client, auth_headers):
        resp = api_client.get("/api/accounts", headers=auth_headers)
        assert resp.status_code == 200
        accounts = resp.json()
        for a in accounts:
            assert "health" in a
            assert "overall" in a["health"]
            assert "validation_status" in a

    def test_validate_single_account(self, api_client, auth_headers):
        resp = api_client.get("/api/accounts", headers=auth_headers)
        accounts = resp.json()
        if not accounts:
            pytest.skip("No accounts to validate")
        first_id = accounts[0]["id"]
        resp = api_client.post(f"/api/accounts/{first_id}/validate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["account_id"] == first_id
        assert data["status"] in ("valid", "invalid", "error")

    def test_validate_nonexistent_account(self, api_client, auth_headers):
        resp = api_client.post("/api/accounts/99999/validate", headers=auth_headers)
        assert resp.status_code == 200  # validate_account_by_id returns error result
        assert resp.json()["status"] == "error"
