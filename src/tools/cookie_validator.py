"""Cookie 实际有效性验证工具

通过 headless Playwright 注入 cookie 并访问各平台的创作者后台，
真实检测 cookie 是否仍然有效（而不仅是比较 expires_at）。

适用于定时任务或手动触发验证。
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from tools.storage import get_temp_dir

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class ValidationResult:
    channel: str
    account_id: int
    status: ValidationStatus
    message: str = ""
    screenshot_path: Optional[str] = None


# 平台验证配置
VALIDATION_CONFIG = {
    "zhihu": {
        "name": "知乎",
        "check_url": "https://zhuanlan.zhihu.com/write",
        "logged_in_indicator": {"url_must_not_contain": ["signin", "login", "unhuman"]},
        "cookie_domain": ".zhihu.com",
    },
    "csdn": {
        "name": "CSDN",
        "check_url": "https://mp.csdn.net/mp_blog/creation/editor",
        "logged_in_indicator": {"url_must_not_contain": ["passport", "login"]},
        "cookie_domain": ".csdn.net",
    },
    "xiaohongshu": {
        "name": "小红书",
        "check_url": "https://creator.xiaohongshu.com/publish/publish",
        "logged_in_indicator": {"url_must_not_contain": ["login"]},
        "cookie_domain": ".xiaohongshu.com",
    },
    "baijiahao": {
        "name": "百家号",
        "check_url": "https://baijiahao.baidu.com/",
        "logged_in_indicator": {"url_must_not_contain": ["passport", "login"]},
        "cookie_domain": ".baidu.com",
    },
    "sohu": {
        "name": "搜狐号",
        "check_url": "https://mp.sohu.com/mpfe/v3/main/news/addarticle",
        "logged_in_indicator": {"url_must_not_contain": ["login"]},
        "cookie_domain": ".sohu.com",
    },
}


def _check_login_status(page, indicators: dict) -> bool:
    url = page.url
    must_not = indicators.get("url_must_not_contain", [])
    for part in must_not:
        if part in url:
            return False
    return True


def validate_cookie(
    channel: str,
    cookie_str: str,
    account_id: int = 0,
    take_screenshot: bool = False,
    workspace: str = ".",
) -> ValidationResult:
    cfg = VALIDATION_CONFIG.get(channel)
    if not cfg:
        return ValidationResult(
            channel=channel, account_id=account_id,
            status=ValidationStatus.ERROR, message=f"不支持的渠道: {channel}"
        )

    if not cookie_str or not cookie_str.strip():
        return ValidationResult(
            channel=channel, account_id=account_id,
            status=ValidationStatus.INVALID, message="Cookie 为空"
        )

    from playwright.sync_api import sync_playwright
    from publisher._browser_mixin import launch_browser_context, parse_cookie_string

    try:
        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            cookies = parse_cookie_string(cookie_str, cfg["cookie_domain"])
            if not cookies:
                browser.close()
                return ValidationResult(
                    channel=channel, account_id=account_id,
                    status=ValidationStatus.INVALID, message="Cookie 解析失败"
                )
            context.add_cookies(cookies)

            page = context.new_page()
            page.goto(cfg["check_url"], wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            is_logged_in = _check_login_status(page, cfg["logged_in_indicator"])

            screenshot_path = None
            if not is_logged_in and take_screenshot:
                import time
                screenshot_dir = get_temp_dir(workspace)
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(screenshot_dir / f"validate_{channel}_{int(time.time())}.png")
                page.screenshot(path=screenshot_path, full_page=True)
                logger.info("Validation screenshot saved: %s", screenshot_path)

            browser.close()

            if is_logged_in:
                return ValidationResult(
                    channel=channel, account_id=account_id,
                    status=ValidationStatus.VALID, message="Cookie 验证通过"
                )
            else:
                return ValidationResult(
                    channel=channel, account_id=account_id,
                    status=ValidationStatus.INVALID,
                    message=f"Cookie 已失效，页面跳转至: {page.url}",
                    screenshot_path=screenshot_path,
                )

    except Exception as e:
        logger.exception("Cookie validation failed for %s", channel)
        return ValidationResult(
            channel=channel, account_id=account_id,
            status=ValidationStatus.ERROR, message=f"验证过程异常: {e}"
        )


def validate_account_by_id(
    account_id: int,
    workspace: str = ".",
    config_dir: str = "config",
    take_screenshot: bool = False,
) -> ValidationResult:
    from paper_library.db import get_session
    from paper_library.models import ChannelAccount
    from publisher.credential_store import decrypt_credential

    with get_session(workspace, config_dir) as session:
        acc = session.get(ChannelAccount, account_id)
        if not acc:
            return ValidationResult(
                channel="", account_id=account_id,
                status=ValidationStatus.ERROR, message="账号不存在"
            )
        result = validate_cookie(
            channel=acc.channel,
            cookie_str=decrypt_credential(acc.credential) or "",
            account_id=acc.id,
            take_screenshot=take_screenshot,
            workspace=workspace,
        )
        now = datetime.datetime.now(datetime.UTC)
        acc.last_validated_at = now
        acc.validation_status = result.status.value
        acc.updated_at = now
        session.add(acc)
        return result


def validate_all_accounts(
    workspace: str = ".",
    config_dir: str = "config",
    take_screenshot: bool = False,
) -> list[ValidationResult]:
    from paper_library.db import get_session
    from paper_library.models import ChannelAccount

    with get_session(workspace, config_dir) as session:
        account_ids = [
            row[0] for row in
            session.query(ChannelAccount.id)
            .filter_by(is_active=True, credential_type="cookie")
            .all()
        ]

    results = []
    for aid in account_ids:
        result = validate_account_by_id(aid, workspace, config_dir, take_screenshot)
        results.append(result)
    return results


def compute_health_status(account: dict) -> dict:
    now = datetime.datetime.now(datetime.UTC)
    expires_at = account.get("expires_at")
    validation_status = account.get("validation_status", "unknown")
    last_validated_at = account.get("last_validated_at")

    if expires_at and isinstance(expires_at, str):
        try:
            expires_at = datetime.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            expires_at = None

    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)

    if last_validated_at and isinstance(last_validated_at, str):
        try:
            last_validated_at = datetime.datetime.fromisoformat(last_validated_at.replace("Z", "+00:00"))
        except Exception:
            last_validated_at = None

    ttl_based_status = "unknown"
    if expires_at:
        days_left = (expires_at - now).total_seconds() / 86400
        if days_left < 0:
            ttl_based_status = "expired"
        elif days_left <= 1:
            ttl_based_status = "critical"
        elif days_left <= 3:
            ttl_based_status = "warning"
        else:
            ttl_based_status = "healthy"

    if validation_status == "valid":
        overall = ttl_based_status if ttl_based_status in ("warning", "critical", "expired") else "healthy"
    elif validation_status == "invalid":
        overall = "expired"
    elif validation_status == "error":
        overall = ttl_based_status if ttl_based_status != "unknown" else "unknown"
    else:
        overall = ttl_based_status

    return {
        "overall": overall,
        "ttl_based": ttl_based_status,
        "validation_status": validation_status,
        "last_validated_at": last_validated_at.isoformat() if last_validated_at else None,
        "message": _health_message(overall),
    }


def _health_message(status: str) -> str:
    messages = {
        "healthy": "Cookie 状态良好",
        "warning": "Cookie 即将过期（3天内），建议重新绑定",
        "critical": "Cookie 即将过期（1天内），请尽快重新绑定",
        "expired": "Cookie 已失效，请重新绑定",
        "unknown": "尚未验证 Cookie 有效性",
    }
    return messages.get(status, "状态未知")
