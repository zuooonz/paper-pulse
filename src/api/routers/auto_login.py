"""Playwright 自动登录 API

提供 headless 浏览器自动登录获取 Cookie 的 HTTP 接口：
- POST /api/accounts/auto-login            启动登录任务
- GET  /api/accounts/auto-login/{task_id}  查询任务状态
- POST /api/accounts/auto-login/{task_id}/cancel     取消任务
- POST /api/accounts/auto-login/{task_id}/submit-sms 提交短信验证码
- POST /api/accounts/auto-login/{task_id}/save       将成功的 cookie 保存到数据库
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tools.playwright_auto_login import (
    start_login_task,
    get_task,
    save_cookie_to_db,
    submit_sms_code_to_task,
    LoginMode,
    TaskStatus,
)
from api.dependencies import get_current_user, require_reviewer

router = APIRouter(prefix="/api/accounts/auto-login", tags=["auto-login"])

SUPPORTED_CHANNELS = ["zhihu", "csdn", "xiaohongshu", "baijiahao", "sohu", "bilibili"]


# ──────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────


class StartLoginRequest(BaseModel):
    channel: str = Field(..., description="目标平台: zhihu/csdn/xiaohongshu/baijiahao/sohu/bilibili")
    mode: str = Field(default="qrcode", description="登录模式: qrcode | sms")
    account_name: str = Field(default="", description="账号显示名称")
    phone: str = Field(default="", description="手机号（sms 模式必填）")


class StartLoginResponse(BaseModel):
    task_id: str
    channel: str
    mode: str
    status: str
    message: str
    qr_code_base64: Optional[str] = None


class TaskStatusResponse(BaseModel):
    task_id: str
    channel: str
    mode: str
    status: str
    message: str
    qr_code_base64: Optional[str] = None
    cookie_str: Optional[str] = None
    account_name: str
    phone: str
    error: Optional[str] = None
    created_at: float
    updated_at: float


class SubmitSmsRequest(BaseModel):
    sms_code: str = Field(..., description="短信验证码")


class SaveCookieRequest(BaseModel):
    account_name: str = Field(default="", description="账号显示名称，为空则使用任务中的名称")


class SaveCookieResponse(BaseModel):
    ok: bool
    account: dict


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────


@router.post("", response_model=StartLoginResponse)
def start_login(
    body: StartLoginRequest,
    user: dict = Depends(require_reviewer),
):
    """启动一个 Playwright 自动登录任务。

    流程：
    1. 后端在后台启动 headless 浏览器
    2. 如果是 qrcode 模式，任务状态会变为 waiting_for_scan，并返回 qr_code_base64
    3. 如果是 sms 模式，任务状态会变为 waiting_for_sms_code，等待用户调用 submit-sms-code
    4. 前端需要轮询 GET /{task_id} 查看状态
    5. 当状态变为 success 时，cookie_str 字段包含完整 cookie
    """
    if body.channel not in SUPPORTED_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的渠道: {body.channel}。支持: {', '.join(SUPPORTED_CHANNELS)}",
        )

    if int(os.environ.get("UVICORN_WORKERS", "1")) > 1:
        raise HTTPException(
            status_code=409,
            detail="扫码/短信登录会话保存在 API 进程内；请将 UVICORN_WORKERS 设为 1 后重试。",
        )

    if body.mode not in ("qrcode", "sms"):
        raise HTTPException(
            status_code=400,
            detail=f"无效的登录模式: {body.mode}。仅支持 qrcode（扫码）或 sms（短信验证码）",
        )

    mode = LoginMode.SMS if body.mode == "sms" else LoginMode.QRCODE

    if mode == LoginMode.SMS and not body.phone:
        raise HTTPException(
            status_code=400,
            detail="sms 模式需要提供 phone（手机号）",
        )

    task = start_login_task(
        channel=body.channel,
        mode=mode,
        account_name=body.account_name.strip(),
        phone=body.phone,
    )

    # 短暂等待，让任务有时间启动
    import time
    for _ in range(15):
        time.sleep(1)
        task = get_task(task.task_id)
        if task and task.status in (
            TaskStatus.WAITING_FOR_SCAN,
            TaskStatus.WAITING_FOR_SMS_CODE,
            TaskStatus.FAILED,
        ):
            break
        if task and task.status == TaskStatus.SUCCESS:
            break

    return StartLoginResponse(
        task_id=task.task_id,
        channel=task.channel,
        mode=task.mode.value,
        status=task.status.value,
        message=task.message,
        qr_code_base64=task.qr_code_base64,
    )


@router.get("/{task_id}", response_model=TaskStatusResponse)
def get_login_status(
    task_id: str,
    user: dict = Depends(get_current_user),
):
    """查询登录任务状态。前端应每 2-3 秒轮询一次。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatusResponse(
        task_id=task.task_id,
        channel=task.channel,
        mode=task.mode.value,
        status=task.status.value,
        message=task.message,
        qr_code_base64=task.qr_code_base64,
        cookie_str=task.cookie_str,
        account_name=task.account_name,
        phone=task.phone,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.post("/{task_id}/cancel")
def cancel_login(
    task_id: str,
    user: dict = Depends(require_reviewer),
):
    """取消正在进行的登录任务。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task.cancel()
    return {"ok": True, "task_id": task_id, "status": task.status.value}


@router.post("/{task_id}/submit-sms-code")
def submit_sms_code(
    task_id: str,
    body: SubmitSmsRequest,
    user: dict = Depends(require_reviewer),
):
    """SMS 模式下，用户提交收到的短信验证码。

    提交后任务会继续执行登录流程，前端继续轮询状态即可。
    """
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != TaskStatus.WAITING_FOR_SMS_CODE:
        raise HTTPException(
            status_code=400,
            detail=f"任务状态为 {task.status.value}，无法提交验证码。",
        )

    result = submit_sms_code_to_task(task_id, body.sms_code)
    if not result:
        raise HTTPException(status_code=400, detail="提交验证码失败")

    return {"ok": True, "task_id": task_id, "status": result.status.value}


@router.post("/{task_id}/save", response_model=SaveCookieResponse)
def save_login_cookie(
    task_id: str,
    body: SaveCookieRequest,
    user: dict = Depends(require_reviewer),
):
    """将成功登录后提取的 cookie 保存到数据库 ChannelAccount 表。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != TaskStatus.SUCCESS:
        raise HTTPException(
            status_code=400,
            detail=f"任务状态为 {task.status.value}，无法保存 cookie。请先等待登录成功。",
        )

    if not task.cookie_str:
        raise HTTPException(status_code=400, detail="Cookie 为空")

    account_name = body.account_name or task.account_name
    if not account_name or not account_name.strip():
        raise HTTPException(
            status_code=400,
            detail="需要提供 account_name（账号自定义名称）",
        )
    account = save_cookie_to_db(
        channel=task.channel,
        account_name=account_name,
        cookie_str=task.cookie_str,
        note=f"Auto-login via API by {user.get('username', 'unknown')}",
    )

    return SaveCookieResponse(ok=True, account=account)
