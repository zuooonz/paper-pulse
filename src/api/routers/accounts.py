"""Channel accounts API router."""
import datetime
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from paper_library.db import get_session
from paper_library.models import ChannelAccount
from paper_library.time_utils import format_api_datetime

from ..config import settings
from ..dependencies import get_current_user, require_reviewer
from publisher.credential_store import encrypt_credential

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
logger = logging.getLogger(__name__)

# Channel metadata (TTL in days, None = permanent)
CHANNEL_META = {
    "csdn":         {"name": "CSDN",      "credential_type": "cookie",       "ttl_days": 30},
    "zhihu":        {"name": "知乎",       "credential_type": "cookie",       "ttl_days": 30},
    "baijiahao":    {"name": "百家号",     "credential_type": "cookie",       "ttl_days": 14},
    "xiaohongshu":  {"name": "小红书",     "credential_type": "cookie",       "ttl_days": 7},
    "sohu":         {"name": "搜狐号",     "credential_type": "cookie",       "ttl_days": 30},
    "bilibili":     {"name": "哔哩哔哩",   "credential_type": "cookie",       "ttl_days": 30},
}


def _account_to_dict(a: ChannelAccount) -> dict:
    now = datetime.datetime.now(datetime.UTC)
    valid: Optional[bool] = None
    if a.expires_at is not None:
        exp = a.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        valid = exp > now

    # 计算健康状态
    from tools.cookie_validator import compute_health_status
    health = compute_health_status({
        "expires_at": format_api_datetime(a.expires_at),
        "valid": valid,
        "last_validated_at": format_api_datetime(a.last_validated_at),
        "validation_status": a.validation_status or "unknown",
    })

    return {
        "id": a.id,
        "channel": a.channel,
        "account_name": a.account_name,
        "credential_type": a.credential_type,
        "expires_at": format_api_datetime(a.expires_at),
        "valid": valid,
        "is_active": a.is_active,
        "note": a.note,
        "bound_at": format_api_datetime(a.bound_at),
        "created_at": format_api_datetime(a.created_at),
        "last_validated_at": format_api_datetime(a.last_validated_at),
        "validation_status": a.validation_status or "unknown",
        "health": health,
    }


# ──────────────────────────────────────────────────────────────
# GET /api/accounts
# ──────────────────────────────────────────────────────────────

@router.get("")
def list_accounts(user: dict = Depends(get_current_user)):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        accounts = (
            session.query(ChannelAccount)
            .filter_by(is_active=True)
            .order_by(ChannelAccount.channel)
            .all()
        )
        return [_account_to_dict(a) for a in accounts]


# ──────────────────────────────────────────────────────────────
# POST /api/accounts
# ──────────────────────────────────────────────────────────────

class CreateAccountRequest(BaseModel):
    channel: str
    account_name: str
    credential_type: str
    credential: Optional[str] = None
    note: Optional[str] = None


@router.post("", status_code=201)
def create_account(
    body: CreateAccountRequest,
    user: dict = Depends(require_reviewer),
):
    meta = CHANNEL_META.get(body.channel)
    if meta is None:
        raise HTTPException(status_code=400, detail=f"未知渠道: {body.channel}")

    try:
        stored_credential = encrypt_credential(body.credential)
    except (RuntimeError, ValueError) as exc:
        # credential_store raises fixed configuration messages (no secrets);
        # admins need them to fix the deployment.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    credential_type = body.credential_type

    now = datetime.datetime.now(datetime.UTC)
    expires_at: Optional[datetime.datetime] = None
    if meta and meta.get("ttl_days") is not None:
        expires_at = now + datetime.timedelta(days=meta["ttl_days"])

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        acc = ChannelAccount(
            channel=body.channel,
            account_name=body.account_name,
            credential_type=credential_type,
            credential=stored_credential,
            expires_at=expires_at,
            note=body.note,
            is_active=True,
            bound_at=now,
            validation_status="unknown",
            last_validated_at=None,
        )
        session.add(acc)
        session.flush()
        return _account_to_dict(acc)


# ──────────────────────────────────────────────────────────────
# DELETE /api/accounts/{id}
# ──────────────────────────────────────────────────────────────

@router.delete("/{account_id}")
def delete_account(
    account_id: int,
    user: dict = Depends(require_reviewer),
):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        acc = session.query(ChannelAccount).get(account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="Account not found")
        acc.is_active = False
        acc.updated_at = datetime.datetime.now(datetime.UTC)
        session.add(acc)
        return {"ok": True, "id": account_id}


# ──────────────────────────────────────────────────────────────
# POST /api/accounts/{id}/validate  — 单账号实际验证
# ──────────────────────────────────────────────────────────────

class ValidateResponse(BaseModel):
    ok: bool
    account_id: int
    channel: str
    status: str
    message: str
    last_validated_at: str | None = None
    validation_status: str = "unknown"
    health: dict | None = None


@router.post("/{account_id}/validate")
def validate_account(
    account_id: int,
    user: dict = Depends(require_reviewer),
):
    """用 headless 浏览器实际验证指定账号的 cookie 是否仍然有效。
    验证完成后返回更新后的账号完整数据，便于前端直接刷新缓存。"""
    from tools.cookie_validator import validate_account_by_id, ValidationStatus

    result = validate_account_by_id(
        account_id,
        workspace=settings.workspace,
        config_dir=settings.config_dir,
    )

    # 重新加载账号，获取更新后的完整数据
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        acc = session.get(ChannelAccount, account_id)
        updated = _account_to_dict(acc) if acc else None

    return ValidateResponse(
        ok=result.status == ValidationStatus.VALID,
        account_id=result.account_id,
        channel=result.channel,
        status=result.status.value,
        message=result.message,
        last_validated_at=updated.get("last_validated_at") if updated else None,
        validation_status=updated.get("validation_status") if updated else "unknown",
        health=updated.get("health") if updated else None,
    )


# ──────────────────────────────────────────────────────────────
# POST /api/accounts/validate-all  — 批量验证
# ──────────────────────────────────────────────────────────────

@router.post("/validate-all")
def validate_all_accounts(
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_reviewer),
):
    """在后台批量验证所有活跃 cookie 账号。"""
    from tools.cookie_validator import validate_all_accounts

    def _run():
        validate_all_accounts(
            workspace=settings.workspace,
            config_dir=settings.config_dir,
        )

    background_tasks.add_task(_run)
    return {"ok": True, "message": "批量验证已在后台启动"}
