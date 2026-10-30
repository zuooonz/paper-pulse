
import pytest
import yaml


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


def test_runtime_yaml_auth_source(monkeypatch, tmp_path):
    from api.config import Settings

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("LOCAL_USERS", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_PASSWORDS", raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    users = {
        "admin": {
            "password": "configured-password",
            "name": "管理员",
            "role": "admin",
        }
    }
    (config_dir / "runtime.yaml").write_text(
        yaml.safe_dump(
            {
                "auth": {
                    "jwt_secret": "configured-jwt-secret",
                    "jwt_algorithm": "HS256",
                    "jwt_expire_days": 7,
                    "allow_plaintext_passwords": False,
                    "local_users": users,
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    configured = Settings()

    assert configured.jwt_secret == "configured-jwt-secret"
    assert configured.jwt_expire_days == 7
    assert configured.allow_plaintext_passwords is False
    assert configured.local_users == users


def test_plaintext_password_compatibility_defaults_to_false(monkeypatch, tmp_path):
    from api.config import Settings

    monkeypatch.delenv("ALLOW_PLAINTEXT_PASSWORDS", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Settings().allow_plaintext_passwords is False


def test_plaintext_password_environment_override(monkeypatch, tmp_path):
    from api.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALLOW_PLAINTEXT_PASSWORDS", "false")
    assert Settings().allow_plaintext_passwords is False


def test_dingtalk_environment_variables_override_yaml(monkeypatch, tmp_path):
    from api.config import Settings

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "runtime.yaml").write_text(
        yaml.safe_dump({"dingtalk": {"enabled": False, "client_id": "yaml-client"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DINGTALK_ENABLED", "true")
    monkeypatch.setenv("DINGTALK_CORP_ID", "env-corp")
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "env-client")
    monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("DINGTALK_AGENT_ID", "env-agent")

    configured = Settings()

    assert configured.dingtalk_enabled is True
    assert configured.dingtalk_corp_id == "env-corp"
    assert configured.dingtalk_client_id == "env-client"
    assert configured.dingtalk_client_secret == "env-secret"
    assert configured.dingtalk_agent_id == "env-agent"


def test_config_loader_expands_yaml_environment_placeholders(monkeypatch):
    from core.config_loader import ConfigLoader

    monkeypatch.setenv("LLM_MODEL", "configured-model")
    assert ConfigLoader._expand_environment({"model": "${LLM_MODEL}"}) == {
        "model": "configured-model"
    }


def test_test_environment_auth_is_loaded_without_exposing_passwords():
    import jwt

    from api.dependencies import USERS, _decode_token, create_token

    assert set(USERS) == {"admin", "reviewer", "reader", "limited"}
    token = create_token("admin")
    decoded = _decode_token(token)
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    assert decoded["role"] == "admin"
    assert "password" not in decoded


def test_dingtalk_token_accepts_a_mapped_username_missing_from_local_users():
    from api.dependencies import _decode_token, create_token

    _add_dingtalk_mapping("account-42", "retired-account", "limited")
    decoded = _decode_token(create_token(
        "retired-account",
        role="limited",
        actor_id="dingtalk:account-42",
        login_source="dingtalk",
    ))

    assert decoded["username"] == "retired-account"
    assert decoded["role"] == "limited"
    assert decoded["actor_id"] == "dingtalk:account-42"
    assert decoded["login_source"] == "dingtalk"


def test_auth_verify_endpoint_accepts_valid_dingtalk_mapping_token():
    from fastapi.testclient import TestClient

    from api.dependencies import create_token
    from api.main import app

    _add_dingtalk_mapping("account-42", "retired-account", "limited")
    token = create_token(
        "retired-account",
        role="limited",
        actor_id="dingtalk:account-42",
        login_source="dingtalk",
    )
    with TestClient(app) as client:
        response = client.post("/api/auth/verify", json={"token": token})

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["username"] == "retired-account"


@pytest.mark.parametrize(
    "login_source,actor_id,role",
    [
        ("local", "dingtalk:account-42", "limited"),
        ("dingtalk", "local:retired-account", "limited"),
        ("dingtalk", "dingtalk:account-42", "unconfigured-role"),
    ],
)
def test_unknown_username_requires_signed_dingtalk_identity_and_configured_role(
    login_source, actor_id, role
):
    from fastapi import HTTPException

    from api.dependencies import _decode_token, create_token

    token = create_token(
        "retired-account",
        role=role,
        actor_id=actor_id,
        login_source=login_source,
    )
    with pytest.raises(HTTPException) as error:
        _decode_token(token)
    assert error.value.status_code == 401


def test_dingtalk_callback_rejects_unconfigured_role_before_signing(monkeypatch):
    from fastapi import HTTPException

    from api.config import settings
    from api.routers import dingtalk_auth

    class FakeDingTalkClient:
        def __init__(self, **_kwargs):
            pass

        def get_user_by_auth_code(self, _auth_code):
            return {"userid": "account-42", "name": "Test", "dept_id_list": [1]}

        def get_user_departments(self, _department_ids):
            return ["test-department"]

    monkeypatch.setattr(settings, "dingtalk_enabled", True)
    monkeypatch.setattr(settings, "dingtalk_corp_id", "corp")
    monkeypatch.setattr(settings, "dingtalk_client_id", "client")
    monkeypatch.setattr(settings, "dingtalk_client_secret", "secret")
    monkeypatch.setattr(dingtalk_auth, "DingTalkClient", FakeDingTalkClient)
    monkeypatch.setattr(dingtalk_auth, "get_internal_username_by_dept", lambda *_args: "retired-account")
    monkeypatch.setattr(dingtalk_auth, "get_role_by_dept", lambda _departments: "unconfigured-role")

    with pytest.raises(HTTPException) as error:
        dingtalk_auth.dingtalk_callback(
            dingtalk_auth.DingTalkCallbackRequest(auth_code="test-code"),
            None,
        )

    assert error.value.status_code == 503
    assert "角色映射" in error.value.detail


def test_placeholder_jwt_secret_is_rejected(monkeypatch):
    from api.config import settings
    from api.dependencies import _jwt_secret

    monkeypatch.setattr(settings, "jwt_secret", "<保持当前值>")

    with pytest.raises(RuntimeError, match="placeholder"):
        _jwt_secret()
