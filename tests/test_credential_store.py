import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pytest


def test_credential_encryption_without_key_rejects_saving_by_default(monkeypatch):
    from publisher.credential_store import decrypt_credential, encrypt_credential

    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_CREDENTIALS", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_PLAINTEXT_CREDENTIALS=true"):
        encrypt_credential("new-cookie")
    assert decrypt_credential("legacy-cookie") == "legacy-cookie"


def test_credential_encryption_can_be_explicitly_enabled_for_local_development(monkeypatch):
    from publisher.credential_store import encrypt_credential

    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ALLOW_PLAINTEXT_CREDENTIALS", "true")
    assert encrypt_credential("local-cookie") == "local-cookie"


def test_account_creation_returns_clear_error_without_encryption_key(monkeypatch):
    from fastapi import HTTPException

    from api.routers.accounts import CreateAccountRequest, create_account

    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_CREDENTIALS", raising=False)

    with pytest.raises(HTTPException) as error:
        create_account(
            CreateAccountRequest(
                channel="csdn", account_name="test", credential_type="cookie",
                credential="new-cookie",
            ),
            user={"username": "admin", "role": "admin"},
        )

    assert error.value.status_code == 503
    assert "CREDENTIALS_ENCRYPTION_KEY" in error.value.detail


def test_account_creation_rejects_removed_channel_as_unknown():
    from fastapi import HTTPException

    from api.routers.accounts import CreateAccountRequest, create_account

    with pytest.raises(HTTPException) as error:
        create_account(
            CreateAccountRequest(
                channel="wechat", account_name="test", credential_type="cookie",
            ),
            user={"username": "admin", "role": "admin"},
        )

    assert error.value.status_code == 400
    assert error.value.detail == "未知渠道: wechat"


def test_account_creation_api_explains_missing_encryption_key(monkeypatch):
    from fastapi.testclient import TestClient

    from api.dependencies import require_reviewer
    from api.main import app

    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_CREDENTIALS", raising=False)
    app.dependency_overrides[require_reviewer] = lambda: {"username": "admin", "role": "admin"}
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/accounts",
                json={
                    "channel": "csdn",
                    "account_name": "test",
                    "credential_type": "cookie",
                    "credential": "new-cookie",
                },
            )
    finally:
        app.dependency_overrides.pop(require_reviewer, None)

    assert response.status_code == 503
    assert "CREDENTIALS_ENCRYPTION_KEY" in response.json()["detail"]


def test_credential_encryption_round_trip_and_plaintext_account_read(monkeypatch):
    from cryptography.fernet import Fernet
    from publisher.credential_store import decrypt_credential, encrypt_credential

    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    encrypted = encrypt_credential("cookie-and-app-secret")
    assert encrypted.startswith("fernet:")
    assert "cookie-and-app-secret" not in encrypted
    assert decrypt_credential(encrypted) == "cookie-and-app-secret"
    assert decrypt_credential("old-plaintext-cookie") == "old-plaintext-cookie"


def test_decrypt_credential_rejects_wrong_key(monkeypatch):
    from cryptography.fernet import Fernet, InvalidToken
    from publisher.credential_store import decrypt_credential, encrypt_credential

    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    encrypted = encrypt_credential("private-cookie")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    with pytest.raises(InvalidToken):
        decrypt_credential(encrypted)


def test_credential_store_key_generation_cli(monkeypatch, capsys):
    import sys
    from cryptography.fernet import Fernet
    from publisher import credential_store

    monkeypatch.setattr(sys, "argv", ["publisher.credential_store", "--generate-key"])
    assert credential_store.main() == 0
    key = capsys.readouterr().out.strip().encode("ascii")
    assert len(Fernet(key)._signing_key) == 16
