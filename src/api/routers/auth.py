from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import jwt

from ..dependencies import USERS, create_token, _decode_token, get_current_user
from ..config import settings
from ..login_limiter import LoginRateLimited, login_limiter
from ..passwords import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    name: str
    role: str
    actor_id: str
    login_source: str


class DingTalkConfigResponse(BaseModel):
    enabled: bool
    corp_id: str
    client_id: str


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    limiter_keys = (f"ip:{client_ip}", f"user:{req.username.strip().lower()}")
    try:
        login_limiter.check(limiter_keys)
    except LoginRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc

    user = USERS.get(req.username)
    if not user or not verify_password(
        req.password,
        user["password"],
        allow_plaintext=settings.allow_plaintext_passwords,
    ):
        from paper_library.governance import record_audit
        record_audit(
            {
                "actor_id": f"local:{req.username or 'unknown'}",
                "username": req.username or "unknown",
                "role": "unknown",
                "name": req.username or "unknown",
                "login_source": "local",
            },
            action="auth.login",
            resource_type="session",
            result="failed",
            request_id=getattr(request.state, "request_id", None),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"reason": "invalid_credentials"},
        )
        login_limiter.record_failure(limiter_keys)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    login_limiter.reset(limiter_keys)
    token = create_token(req.username)
    from paper_library.governance import record_audit
    record_audit(
        {
            "actor_id": f"local:{req.username}",
            "username": req.username,
            "role": user["role"],
            "name": user["name"],
            "login_source": "local",
        },
        action="auth.login",
        resource_type="session",
        result="success",
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return LoginResponse(
        access_token=token,
        name=user["name"],
        role=user["role"],
        actor_id=f"local:{req.username}",
        login_source="local",
    )


@router.get("/dingtalk/config", response_model=DingTalkConfigResponse)
def get_dingtalk_config():
    """返回钉钉登录的公开配置（不包含 client_secret）"""
    return DingTalkConfigResponse(
        enabled=settings.dingtalk_enabled,
        corp_id=settings.dingtalk_corp_id,
        client_id=settings.dingtalk_client_id,
    )


class TokenVerifyRequest(BaseModel):
    token: str


class TokenVerifyResponse(BaseModel):
    valid: bool
    username: str = ""
    name: str = ""
    role: str = ""
    actor_id: str = ""
    login_source: str = ""
    department: str = ""


@router.post("/verify", response_model=TokenVerifyResponse)
def verify_token(request: TokenVerifyRequest):
    """验证 JWT token 是否有效，返回用户信息"""
    try:
        user_info = _decode_token(request.token)
        return TokenVerifyResponse(
            valid=True,
            username=user_info["username"],
            name=user_info["name"],
            role=user_info["role"],
            actor_id=user_info["actor_id"],
            login_source=user_info["login_source"],
            department=user_info["department"],
        )
    except (jwt.PyJWTError, HTTPException):
        return TokenVerifyResponse(valid=False)


@router.post("/logout")
def logout(request: Request, user: dict = Depends(get_current_user)):
    from paper_library.governance import record_audit
    record_audit(
        user,
        action="auth.logout",
        resource_type="session",
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )
    return {"success": True}
