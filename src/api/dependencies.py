import datetime
import logging
import threading

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from .config import settings
from .passwords import is_password_hash

logger = logging.getLogger(__name__)
_plaintext_warning_lock = threading.Lock()
_warned_plaintext_passwords = False

security = HTTPBearer()

def _load_local_users() -> dict[str, dict[str, str]]:
    """Validate and normalize local fallback users from runtime configuration."""
    users: dict[str, dict[str, str]] = {}
    for username, raw in settings.local_users.items():
        if not isinstance(raw, dict):
            raise RuntimeError(f"auth.local_users.{username} must be a mapping")
        password = str(raw.get("password") or "")
        name = str(raw.get("name") or "")
        role = str(raw.get("role") or "")
        if not username or not password or not name or not role:
            raise RuntimeError(f"auth.local_users.{username} is incomplete")
        users[str(username)] = {"password": password, "name": name, "role": role}
    return users


USERS = _load_local_users()


def validate_auth_configuration() -> None:
    """Fail at startup instead of returning 500 on the first login."""
    _jwt_secret()
    if not USERS:
        raise RuntimeError(
            "No local users configured; set LOCAL_USERS or LOCAL_ADMIN_PASSWORD "
            "(and other LOCAL_*_PASSWORD values), or mount config/runtime.yaml"
        )
    if not settings.allow_plaintext_passwords:
        plaintext_users = [
            username for username, user in USERS.items()
            if not is_password_hash(user["password"])
        ]
        if plaintext_users:
            raise RuntimeError(
                "Plaintext local passwords are configured while "
                "ALLOW_PLAINTEXT_PASSWORDS=false; hash them with "
                "python -m api.hash_password or explicitly enable the flag"
            )


def warn_if_plaintext_passwords_enabled() -> None:
    """Warn once at startup when legacy plaintext local passwords remain."""
    global _warned_plaintext_passwords
    if not settings.allow_plaintext_passwords:
        return
    if not any(not is_password_hash(user["password"]) for user in USERS.values()):
        return
    with _plaintext_warning_lock:
        if not _warned_plaintext_passwords:
            logger.warning(
                "Plaintext local account passwords are enabled; hash them with "
                "python -m api.hash_password and set auth.allow_plaintext_passwords: false"
            )
            _warned_plaintext_passwords = True


def _jwt_secret() -> str:
    secret = settings.jwt_secret.strip()
    placeholder = (
        (secret.startswith("${") and secret.endswith("}"))
        or (secret.startswith("<") and secret.endswith(">"))
        or "placeholder" in secret.lower()
        or "change-me" in secret.lower()
    )
    if len(secret) < 24 or placeholder:
        raise RuntimeError(
            "JWT secret is missing, too short, or a placeholder; "
            "set auth.jwt_secret or JWT_SECRET"
        )
    return secret


def create_token(
    username: str,
    role: str | None = None,
    name: str | None = None,
    *,
    actor_id: str | None = None,
    login_source: str = "local",
    department: str | None = None,
) -> str:
    """生成 JWT token

    Args:
        username: 用户名
        role: 可选的角色（覆盖 USERS 字典中的默认角色）
        name: 可选的用户姓名
    """
    exp = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=settings.jwt_expire_days)
    payload = {
        "sub": username,
        "exp": exp,
        "actor_id": actor_id or f"local:{username}",
        "login_source": login_source,
        "ver": settings.jwt_token_version,
    }
    if role:
        payload["role"] = role
    if name:
        payload["name"] = name
    if department:
        payload["department"] = department
    return jwt.encode(
        payload,
        _jwt_secret(),
        algorithm=settings.jwt_algorithm,
    )


def _decode_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        _jwt_secret(),
        algorithms=[settings.jwt_algorithm],
    )
    if payload.get("ver", 1) != settings.jwt_token_version:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("sub")
    login_source = payload.get("login_source") or "local"
    actor_id = payload.get("actor_id") or f"local:{username}"
    if not isinstance(username, str) or not username or not isinstance(login_source, str):
        raise HTTPException(status_code=401, detail="Invalid token")
    if not isinstance(actor_id, str):
        raise HTTPException(status_code=401, detail="Invalid token")
    configured_user = USERS.get(username)
    if login_source not in {"local", "dingtalk"}:
        raise HTTPException(status_code=401, detail="Invalid token")

    if login_source == "dingtalk":
        from paper_library.db import get_session
        from paper_library.models import DingTalkUser

        dingtalk_id = actor_id.removeprefix("dingtalk:")
        if not actor_id.startswith("dingtalk:") or not dingtalk_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
            mapping = session.query(DingTalkUser).filter(
                DingTalkUser.dingtalk_userid == dingtalk_id
            ).first()
        allowed_roles = {user["role"] for user in USERS.values()}
        if (
            mapping is None
            or mapping.role != payload.get("role")
            or mapping.role not in allowed_roles
        ):
            # Deleted mappings and role downgrades invalidate existing tokens.
            raise HTTPException(status_code=401, detail="Invalid token")
        return {
            "username": mapping.internal_username,
            "name": mapping.display_name or username,
            "role": mapping.role,
            "actor_id": actor_id,
            "login_source": "dingtalk",
            "department": payload.get("department") or "",
        }

    if configured_user is None:
        allowed_roles = {user["role"] for user in USERS.values()}
        if (
            login_source != "dingtalk"
            or not isinstance(actor_id, str)
            or not actor_id.startswith("dingtalk:")
            or not isinstance(payload.get("role"), str)
            or payload["role"] not in allowed_roles
            or not isinstance(username, str)
            or not username
        ):
            raise HTTPException(status_code=401, detail="Invalid token")
        user_info = {"name": username, "role": payload["role"]}
    else:
        user_info = {
            "name": configured_user["name"],
            "role": configured_user["role"],
        }
        if login_source == "dingtalk":
            allowed_roles = {user["role"] for user in USERS.values()}
            token_role = payload.get("role")
            if not isinstance(token_role, str) or token_role not in allowed_roles:
                raise HTTPException(status_code=401, detail="Invalid token")
            user_info["role"] = token_role
    if payload.get("name"):
        user_info["name"] = payload["name"]
    return {
        "username": username,
        **user_info,
        "actor_id": actor_id,
        "login_source": login_source,
        "department": payload.get("department") or "",
    }


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        return _decode_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_content_access(user: dict = Depends(get_current_user)) -> dict:
    """Allow the content workspace (admin/reviewer/viewer), never limited users."""
    if user["role"] not in ("admin", "reviewer", "viewer"):
        raise HTTPException(status_code=403, detail="Content access required")
    return user


def require_reviewer(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] not in ("admin", "reviewer"):
        raise HTTPException(status_code=403, detail="Reviewer role required")
    return user


def require_uploader(user: dict = Depends(get_current_user)) -> dict:
    """允许管理员和查看者上传或导入论文。"""
    if user["role"] not in ("admin", "viewer"):
        raise HTTPException(status_code=403, detail="Uploader role required")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_paper_editor(user: dict = Depends(get_current_user)) -> dict:
    """允许管理员和查看者编辑或删除论文。"""
    if user["role"] not in ("admin", "viewer"):
        raise HTTPException(status_code=403, detail="Paper editor role required (admin or viewer)")
    return user
