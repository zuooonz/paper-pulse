"""钉钉 OAuth 回调接口"""
from pathlib import Path
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from ..dingtalk_client import DingTalkClient
from ..dependencies import USERS, create_token
from ..config import settings
from ..error_responses import internal_http_error
from core.service_config import runtime_config
from paper_library.db import get_session
from paper_library.models import DingTalkUser

router = APIRouter(prefix="/api/auth/dingtalk", tags=["dingtalk"])
logger = logging.getLogger(__name__)


def _dingtalk_role_config() -> dict:
    config_dir = Path(settings.workspace) / settings.config_dir
    runtime = runtime_config(config_dir)
    dingtalk = runtime.get("dingtalk", {}) or {}
    role_config = dingtalk.get("role_mapping", {}) or {}
    if not role_config:
        role_config = (runtime.get("auth", {}) or {}).get("roles", {}) or {}
    return role_config if isinstance(role_config, dict) else {}


def get_internal_username_by_dept(dept_names: list, user_name: str = "") -> str:
    """Resolve a configured generic account for a DingTalk user."""
    role_config = _dingtalk_role_config()
    default_username = str(role_config.get("default_username") or "")
    department_map = role_config.get("departments", {}) or {}
    for department in dept_names:
        value = department_map.get(department) if isinstance(department_map, dict) else None
        if isinstance(value, dict) and value.get("username"):
            return str(value["username"])
    return default_username


def get_role_by_dept(dept_names: list) -> str:
    """Return a role from optional configuration, with a generic viewer fallback."""
    role_config = _dingtalk_role_config()
    department_map = role_config.get("departments", {}) or {}
    for department in dept_names:
        value = department_map.get(department) if isinstance(department_map, dict) else None
        if isinstance(value, dict) and value.get("role"):
            return str(value["role"])
    return str(role_config.get("default_role") or "")


class DingTalkCallbackRequest(BaseModel):
    auth_code: str


class DingTalkCallbackResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    name: str
    role: str
    dingtalk_userid: str
    actor_id: str
    login_source: str = "dingtalk"
    department: str = ""


@router.post("/callback", response_model=DingTalkCallbackResponse)
def dingtalk_callback(request: DingTalkCallbackRequest, http_request: Request):
    """钉钉 OAuth 回调接口

    流程:
    1. 接收前端传来的 authCode
    2. 调用钉钉 API 获取用户信息和部门信息
    3. 根据部门名称自动确定内部账号
    4. 查询或创建用户映射
    5. 生成 JWT token
    6. 返回 token 和用户信息
    """
    if not settings.dingtalk_enabled:
        raise HTTPException(status_code=400, detail="钉钉登录未启用")

    # 1. 调用钉钉 API 获取用户信息
    if not settings.dingtalk_corp_id or not settings.dingtalk_client_id or not settings.dingtalk_client_secret:
        raise HTTPException(status_code=503, detail="钉钉登录配置不完整")

    try:
        client = DingTalkClient(
            app_key=settings.dingtalk_client_id,
            app_secret=settings.dingtalk_client_secret,
        )
        user_info = client.get_user_by_auth_code(request.auth_code)
    except Exception as e:
        from paper_library.governance import record_audit
        record_audit(
            {
                "actor_id": "dingtalk:unknown",
                "username": "unknown",
                "role": "unknown",
                "name": "",
                "login_source": "dingtalk",
            },
            action="auth.login",
            resource_type="session",
            result="failed",
            request_id=getattr(http_request.state, "request_id", None),
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
            details={"reason": "dingtalk_auth_failed", "error_type": type(e).__name__},
        )
        raise internal_http_error(
            logger,
            "DingTalk authentication",
            e,
            status_code=401,
            detail="钉钉认证失败，请稍后重试",
        ) from e

    dingtalk_userid = user_info.get("userid")
    if not dingtalk_userid:
        raise HTTPException(status_code=401, detail="未获取到用户 ID")

    # 2. 获取部门信息，确定内部账号
    dept_id_list = user_info.get("dept_id_list", [])
    dept_names = client.get_user_departments(dept_id_list)
    dingtalk_name = user_info.get("name", "")
    internal_username = get_internal_username_by_dept(dept_names, dingtalk_name)
    mapped_role = get_role_by_dept(dept_names)
    if not internal_username or not mapped_role:
        raise HTTPException(status_code=403, detail="当前钉钉部门未配置访问角色")
    configured_roles = {user["role"] for user in USERS.values()}
    if mapped_role not in configured_roles:
        raise HTTPException(status_code=503, detail="钉钉角色映射必须使用 auth.local_users 中已配置的角色")

    # 3. 查询用户映射，如果不存在则自动创建
    with get_session() as session:
        mapping_created = False
        dingtalk_user = session.query(DingTalkUser).filter(
            DingTalkUser.dingtalk_userid == dingtalk_userid
        ).first()

        dingtalk_name = user_info.get("name", "")

        if not dingtalk_user:
            # 首次登录，根据部门自动创建映射
            dingtalk_user = DingTalkUser(
                dingtalk_userid=dingtalk_userid,
                internal_username=internal_username,
                role=mapped_role,
                display_name=dingtalk_name,
            )
            session.add(dingtalk_user)
            session.commit()
            session.refresh(dingtalk_user)
            mapping_created = True
        else:
            # 部门/角色/姓名可能已变化；每次登录同步，避免调岗后保留旧权限。
            changed = (
                dingtalk_user.internal_username != internal_username
                or dingtalk_user.role != mapped_role
                or (dingtalk_name and dingtalk_user.display_name != dingtalk_name)
            )
            if changed:
                dingtalk_user.internal_username = internal_username
                dingtalk_user.role = mapped_role
                if dingtalk_name:
                    dingtalk_user.display_name = dingtalk_name
                session.commit()
                session.refresh(dingtalk_user)

        if dingtalk_user.role not in configured_roles:
            raise HTTPException(status_code=503, detail="钉钉用户映射角色未在 auth.local_users 中配置")

        # 4. 生成 JWT token（使用数据库中的实际角色）
        user_name = user_info.get("name", "") or dingtalk_user.display_name or ""
        token = create_token(
            dingtalk_user.internal_username,
            role=dingtalk_user.role,
            name=user_name,
            actor_id=f"dingtalk:{dingtalk_userid}",
            login_source="dingtalk",
            department=" / ".join(dept_names),
        )

        from paper_library.governance import record_audit
        audit_actor = {
            "actor_id": f"dingtalk:{dingtalk_userid}",
            "username": dingtalk_user.internal_username,
            "role": dingtalk_user.role,
            "name": user_name,
            "login_source": "dingtalk",
            "department": " / ".join(dept_names),
        }
        if mapping_created:
            record_audit(
                audit_actor,
                action="governance.identity_mapping_create",
                resource_type="dingtalk_user",
                resource_id=dingtalk_user.id,
                result="success",
                request_id=getattr(http_request.state, "request_id", None),
                ip_address=http_request.client.host if http_request.client else None,
                user_agent=http_request.headers.get("user-agent"),
                details={"internal_username": dingtalk_user.internal_username, "role": dingtalk_user.role},
            )
        record_audit(
            audit_actor,
            action="auth.login",
            resource_type="session",
            result="success",
            request_id=getattr(http_request.state, "request_id", None),
            ip_address=http_request.client.host if http_request.client else None,
            user_agent=http_request.headers.get("user-agent"),
        )

        return DingTalkCallbackResponse(
            access_token=token,
            username=dingtalk_user.internal_username,
            name=user_info.get("name", ""),
            role=dingtalk_user.role,
            dingtalk_userid=dingtalk_userid,
            actor_id=f"dingtalk:{dingtalk_userid}",
            department=" / ".join(dept_names),
        )
