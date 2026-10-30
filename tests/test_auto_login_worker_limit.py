import pytest
from fastapi import HTTPException

from api.routers.auto_login import StartLoginRequest, start_login


def test_in_process_login_rejects_multiple_api_workers(monkeypatch):
    monkeypatch.setenv("UVICORN_WORKERS", "2")

    with pytest.raises(HTTPException) as exc:
        start_login(StartLoginRequest(channel="zhihu", mode="qrcode"), user={})

    assert exc.value.status_code == 409
