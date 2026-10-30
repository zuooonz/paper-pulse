"""钉钉用户管理接口"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Optional
from paper_library.db import get_session
from paper_library.models import DingTalkUser
from ..dependencies import require_admin, USERS
import datetime
from paper_library.governance import record_audit
from paper_library.time_utils import to_local

router = APIRouter(prefix="/api/auth/dingtalk", tags=["dingtalk-admin"])


def _get_role_for_username(username: str) -> str:
    """根据内部账号获取对应的权限角色。未知账号不再默认 viewer。"""
    if username in USERS:
        return USERS[username]["role"]
    raise HTTPException(status_code=400, detail="internal_username 未在 auth.local_users 中配置")


class DingTalkUserResponse(BaseModel):
    id: int
    dingtalk_userid: str
    internal_username: str
    role: str
    display_name: Optional[str] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DingTalkUserCreateRequest(BaseModel):
    dingtalk_userid: str
    internal_username: str
    role: Optional[str] = None
    display_name: Optional[str] = None


class DingTalkUserUpdateRequest(BaseModel):
    internal_username: Optional[str] = None
    role: Optional[str] = None
    display_name: Optional[str] = None


@router.get("/users", response_model=List[DingTalkUserResponse])
def list_dingtalk_users(user: dict = Depends(require_admin)):
    """列出所有钉钉用户映射"""
    with get_session() as session:
        users = session.query(DingTalkUser).all()
        return [
            DingTalkUserResponse(
                id=u.id,
                dingtalk_userid=u.dingtalk_userid,
                internal_username=u.internal_username,
                role=u.role,
                display_name=u.display_name,
                created_at=to_local(u.created_at),
                updated_at=to_local(u.updated_at)
            )
            for u in users
        ]


@router.post("/users", response_model=DingTalkUserResponse)
def create_dingtalk_user(
    request: DingTalkUserCreateRequest,
    http_request: Request,
    user: dict = Depends(require_admin)
):
    """创建钉钉用户映射（角色根据部门自动设置）"""
    with get_session() as session:
        existing = session.query(DingTalkUser).filter(
            DingTalkUser.dingtalk_userid == request.dingtalk_userid
        ).first()

        if existing:
            raise HTTPException(status_code=400, detail="该钉钉用户已存在")

        # 根据部门账号自动设置角色
        role = request.role or _get_role_for_username(request.internal_username)
        if role not in {entry["role"] for entry in USERS.values()}:
            raise HTTPException(status_code=400, detail="role 未在 auth.local_users 中配置")

        new_user = DingTalkUser(
            dingtalk_userid=request.dingtalk_userid,
            internal_username=request.internal_username,
            role=role,
            display_name=request.display_name,
        )
        session.add(new_user)
        session.commit()
        session.refresh(new_user)

        payload = DingTalkUserResponse(
            id=new_user.id,
            dingtalk_userid=new_user.dingtalk_userid,
            internal_username=new_user.internal_username,
            role=new_user.role,
            display_name=new_user.display_name,
            created_at=to_local(new_user.created_at),
            updated_at=to_local(new_user.updated_at)
        )
    record_audit(
        user,
        action="auth.user_mapping_create",
        resource_type="dingtalk_user",
        resource_id=payload.id,
        result="success",
        details={"dingtalk_userid": payload.dingtalk_userid, "internal_username": payload.internal_username, "role": payload.role},
        request_id=getattr(http_request.state, "request_id", None),
        ip_address=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
    )
    return payload


@router.put("/users/{user_id}", response_model=DingTalkUserResponse)
def update_dingtalk_user(
    user_id: int,
    request: DingTalkUserUpdateRequest,
    http_request: Request,
    current_user: dict = Depends(require_admin)
):
    """更新钉钉用户映射（修改部门时自动更新角色）"""
    with get_session() as session:
        dingtalk_user = session.query(DingTalkUser).filter(
            DingTalkUser.id == user_id
        ).first()

        if not dingtalk_user:
            raise HTTPException(status_code=404, detail="用户映射不存在")

        before = {
            "internal_username": dingtalk_user.internal_username,
            "role": dingtalk_user.role,
            "display_name": dingtalk_user.display_name,
        }
        if request.internal_username is not None:
            dingtalk_user.internal_username = request.internal_username
            # 修改部门时自动更新角色
            dingtalk_user.role = _get_role_for_username(request.internal_username)
        if request.role is not None:
            if request.role not in {entry["role"] for entry in USERS.values()}:
                raise HTTPException(status_code=400, detail="role 未在 auth.local_users 中配置")
            dingtalk_user.role = request.role
        if request.display_name is not None:
            dingtalk_user.display_name = request.display_name

        session.commit()
        session.refresh(dingtalk_user)

        payload = DingTalkUserResponse(
            id=dingtalk_user.id,
            dingtalk_userid=dingtalk_user.dingtalk_userid,
            internal_username=dingtalk_user.internal_username,
            role=dingtalk_user.role,
            display_name=dingtalk_user.display_name,
            created_at=to_local(dingtalk_user.created_at),
            updated_at=to_local(dingtalk_user.updated_at)
        )
    record_audit(
        current_user,
        action="auth.user_mapping_update",
        resource_type="dingtalk_user",
        resource_id=user_id,
        result="success",
        details={"before": before, "after": {"internal_username": payload.internal_username, "role": payload.role, "display_name": payload.display_name}},
        request_id=getattr(http_request.state, "request_id", None),
        ip_address=http_request.client.host if http_request.client else None,
        user_agent=http_request.headers.get("user-agent"),
    )
    return payload


@router.delete("/users/{user_id}")
def delete_dingtalk_user(
    user_id: int,
    request: Request,
    user: dict = Depends(require_admin)
):
    """删除钉钉用户映射"""
    with get_session() as session:
        dingtalk_user = session.query(DingTalkUser).filter(
            DingTalkUser.id == user_id
        ).first()

        if not dingtalk_user:
            raise HTTPException(status_code=404, detail="用户映射不存在")

        before = {
            "dingtalk_userid": dingtalk_user.dingtalk_userid,
            "internal_username": dingtalk_user.internal_username,
            "role": dingtalk_user.role,
            "display_name": dingtalk_user.display_name,
        }
        session.delete(dingtalk_user)
        session.commit()
    record_audit(
        user,
        action="auth.user_mapping_delete",
        resource_type="dingtalk_user",
        resource_id=user_id,
        result="success",
        details={"before": before},
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"message": "删除成功"}
