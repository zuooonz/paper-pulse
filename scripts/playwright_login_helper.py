"""Playwright 自动登录获取 Cookie 工具

支持平台：zhihu, csdn, xiaohongshu, baijiahao, sohu
使用方法：
    python scripts/playwright_login_helper.py zhihu --account-name "我的知乎号"
    python scripts/playwright_login_helper.py csdn --account-name "我的CSDN"
    python scripts/playwright_login_helper.py xiaohongshu --account-name "小红书官方"
    python scripts/playwright_login_helper.py baijiahao --account-name "百家号"
    python scripts/playwright_login_helper.py sohu --account-name "搜狐号"

流程：
    1. 自动打开系统 Chrome（有头模式）
    2. 跳转到对应平台登录页/创作者页
    3. 用户手动扫码或密码登录
    4. 脚本自动检测登录成功
    5. 提取完整 Cookie 字符串
    6. 保存到数据库 ChannelAccount 表

注意：
    - 请确保已安装 Playwright Python 包及其浏览器
    - Windows 本地测试推荐使用系统 Chrome（channel="chrome"）
    - 登录过程中不要关闭浏览器窗口
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from pathlib import Path

# 将 src 加入路径
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from core.cli import add_db_arguments, prepare_database
from core.service_config import app_timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 平台配置
# ──────────────────────────────────────────────────────────────
PLATFORM_CONFIG = {
    "zhihu": {
        "name": "知乎",
        "start_url": "https://zhuanlan.zhihu.com/write",
        "login_indicators": {
            "success_url_contains": ["zhuanlan.zhihu.com/write"],
            "success_url_not_contains": ["signin", "login", "unhuman"],
        },
        "cookie_domain": ".zhihu.com",
        "required_cookies": ["z_c0"],  # 用于校验的核心 cookie
    },
    "csdn": {
        "name": "CSDN",
        "start_url": "https://mp.csdn.net/mp_blog/creation/editor",
        "login_indicators": {
            "success_url_contains": ["csdn.net"],
            "success_url_not_contains": ["passport", "login"],
        },
        "cookie_domain": ".csdn.net",
        "required_cookies": [],
    },
    "xiaohongshu": {
        "name": "小红书",
        "start_url": "https://creator.xiaohongshu.com/publish/publish",
        "login_indicators": {
            "success_url_contains": ["creator.xiaohongshu.com"],
            "success_url_not_contains": ["login", "signin"],
        },
        "cookie_domain": ".xiaohongshu.com",
        "required_cookies": [],
    },
    "baijiahao": {
        "name": "百家号",
        "start_url": "https://baijiahao.baidu.com/",
        "login_indicators": {
            "success_url_contains": ["baijiahao.baidu.com"],
            "success_url_not_contains": ["passport", "login"],
        },
        "cookie_domain": ".baidu.com",
        "required_cookies": [],
    },
    "sohu": {
        "name": "搜狐号",
        "start_url": "https://mp.sohu.com/mpfe/v3/main/news/addarticle",
        "login_indicators": {
            "success_url_contains": ["mp.sohu.com"],
            "success_url_not_contains": ["login"],
        },
        "cookie_domain": ".sohu.com",
        "required_cookies": [],
    },
}


def _build_stealth_script() -> str:
    """反检测脚本（与 _browser_mixin.py 保持一致）"""
    return """
    (function () {
        'use strict';
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        try {
            for (let key in window) { if (key.match(/cdc_[a-zA-Z0-9]/)) delete window[key]; }
        } catch(e) {}
        window.chrome = { runtime: {} };
        try { window.navigator.chrome = { runtime: {} }; } catch(e) {}
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    })();
    """


def _launch_browser(p):
    """启动有头浏览器（本地扫码登录需要可见窗口）"""
    browser = p.chromium.launch(
        headless=False,
        channel="chrome",  # 使用系统 Chrome，指纹更真实
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        locale="zh-CN",
        timezone_id=app_timezone(),
    )
    context.add_init_script(_build_stealth_script())
    return browser, context


def _is_logged_in(page, indicators: dict) -> bool:
    """根据 URL 规则检测是否已登录"""
    url = page.url
    must_have = indicators.get("success_url_contains", [])
    must_not_have = indicators.get("success_url_not_contains", [])

    for part in must_have:
        if part not in url:
            return False
    for part in must_not_have:
        if part in url:
            return False
    return True


def _save_to_db(channel: str, account_name: str, cookie_str: str) -> None:
    """保存到 ChannelAccount 表"""
    import datetime
    from paper_library.db import get_session
    from paper_library.models import ChannelAccount
    from publisher.credential_store import encrypt_credential

    # 默认 TTL（与 accounts API 保持一致）
    TTL_MAP = {
        "csdn": 30,
        "zhihu": 30,
        "baijiahao": 14,
        "xiaohongshu": 7,
        "sohu": 30,
    }
    now = datetime.datetime.now(datetime.UTC)
    expires_at = now + datetime.timedelta(days=TTL_MAP.get(channel, 30))

    # 先停用同渠道旧账号（一渠道只保留一个活跃账号，避免冲突）
    with get_session(".", "config") as session:
        old_accounts = (
            session.query(ChannelAccount)
            .filter_by(channel=channel, is_active=True)
            .all()
        )
        for old in old_accounts:
            old.is_active = False
            old.updated_at = now

        # 插入新账号
        acc = ChannelAccount(
            channel=channel,
            account_name=account_name or f"{channel}_auto",
            credential_type="cookie",
            credential=encrypt_credential(cookie_str),
            expires_at=expires_at,
            is_active=True,
            bound_at=now,
            note=f"Playwright 自动获取 @ {now.isoformat()}",
        )
        session.add(acc)
        session.flush()
        logger.info("已保存到数据库: channel=%s, account_id=%d, name=%s", channel, acc.id, acc.account_name)


def login_and_extract_cookie(channel: str, account_name: str, timeout_seconds: int = 120) -> str | None:
    """打开浏览器让用户登录，然后提取 cookie"""
    cfg = PLATFORM_CONFIG.get(channel)
    if not cfg:
        logger.error("不支持的渠道: %s。支持: %s", channel, ", ".join(PLATFORM_CONFIG.keys()))
        return None

    from playwright.sync_api import sync_playwright

    logger.info("【%s】正在启动 Chrome…", cfg["name"])
    logger.info("请在新打开的浏览器窗口中完成登录（扫码或密码）")

    with sync_playwright() as p:
        browser, context = _launch_browser(p)
        page = context.new_page()

        # 访问起始页面
        page.goto(cfg["start_url"], wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # 如果已经登录，直接提取
        if _is_logged_in(page, cfg["login_indicators"]):
            logger.info("检测到已是登录状态，直接提取 Cookie")
        else:
            logger.info("当前未登录，请在 %d 秒内完成登录…", timeout_seconds)
            # 轮询检测登录状态
            logged_in = False
            start = time.time()
            while time.time() - start < timeout_seconds:
                time.sleep(2)
                try:
                    if _is_logged_in(page, cfg["login_indicators"]):
                        logged_in = True
                        logger.info("✅ 检测到登录成功！")
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（login_and_extract_cookie）', exc_info=True)
                    pass
                # 每 10 秒提示一次
                elapsed = int(time.time() - start)
                if elapsed % 10 == 0:
                    logger.info("等待登录中… 已等待 %d 秒", elapsed)

            if not logged_in:
                logger.warning("⏰ 超时未检测到登录成功，将提取当前 Cookie（可能不完整）")

        # 额外等待，确保 cookie 完全写入
        page.wait_for_timeout(3000)

        # 提取 cookie
        cookies = context.cookies()
        from tools.cookie_utils import format_cookie_string

        cookie_str = format_cookie_string(cookies)

        # 校验核心 cookie
        missing = []
        for req in cfg.get("required_cookies", []):
            if req not in cookie_str:
                missing.append(req)
        if missing:
            logger.warning("Cookie 中缺少关键字段: %s", missing)

        logger.info("提取到 %d 条 cookie，总长度 %d", len(cookies), len(cookie_str))
        browser.close()
        return cookie_str


def main():
    parser = argparse.ArgumentParser(description="Playwright 自动登录获取 Cookie")
    parser.add_argument("channel", choices=list(PLATFORM_CONFIG.keys()), help="目标平台")
    parser.add_argument("--account-name", "-n", default="", help="账号显示名称")
    parser.add_argument("--timeout", "-t", type=int, default=120, help="登录等待超时（秒）")
    parser.add_argument("--dry-run", action="store_true", help="仅提取并打印 Cookie，不保存到数据库")
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    cookie_str = login_and_extract_cookie(args.channel, args.account_name, args.timeout)
    if not cookie_str:
        sys.exit(1)

    # 打印前 200 字符供用户确认
    preview = cookie_str[:200] + "…" if len(cookie_str) > 200 else cookie_str
    logger.info("Cookie 预览: %s", preview)

    if args.dry_run:
        logger.info("--dry-run 模式，跳过数据库保存")
        print("\n=== FULL COOKIE ===\n")
        print(cookie_str)
        return

    # 确认保存
    ans = input(f"\n是否将 Cookie 保存到数据库（渠道: {args.channel}）？[y/N] ").strip().lower()
    if ans in ("y", "yes"):
        _save_to_db(args.channel, args.account_name or f"{args.channel}_auto", cookie_str)
        logger.info("✅ 保存完成！")
    else:
        logger.info("已取消保存")
        print("\n=== FULL COOKIE ===\n")
        print(cookie_str)


if __name__ == "__main__":
    main()
