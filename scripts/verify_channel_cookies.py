"""验证各平台 Cookie 有效性（仅登录检测，不发布内容）

使用方法：
    python scripts/verify_channel_cookies.py          # 验证所有渠道
    python scripts/verify_channel_cookies.py zhihu    # 只验证知乎
    python scripts/verify_channel_cookies.py csdn baijiahao  # 验证多个

输出：
    - 各渠道登录状态（成功/失败）
    - 当前页面 URL
    - 页面关键元素检测结果
    - 截图保存到配置的临时目录
"""
from __future__ import annotations
import argparse
import datetime
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tools.storage import get_temp_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

from paper_library.db import get_session
from paper_library.models import ChannelAccount
from publisher.credential_store import decrypt_credential
from publisher._browser_mixin import launch_browser_context

VERIFY_CONFIG = {
    "zhihu": {
        "name": "知乎",
        "url": "https://zhuanlan.zhihu.com/write",
        "success_url_contains": ["zhuanlan.zhihu.com/write"],
        "fail_url_contains": ["signin", "login", "unhuman"],
        "cookie_domain": ".zhihu.com",
    },
    "csdn": {
        "name": "CSDN",
        "url": "https://mp.csdn.net/mp_blog/creation/editor",
        "success_url_contains": ["csdn.net", "editor"],
        "fail_url_contains": ["passport", "login"],
        "cookie_domain": ".csdn.net",
    },
    "xiaohongshu": {
        "name": "小红书",
        "url": "https://creator.xiaohongshu.com/publish/publish",
        "success_url_contains": ["creator.xiaohongshu.com"],
        "fail_url_contains": ["login"],
        "cookie_domain": ".xiaohongshu.com",
    },
    "baijiahao": {
        "name": "百家号",
        "url": "https://baijiahao.baidu.com/",
        "success_url_contains": ["baijiahao.baidu.com"],
        "fail_url_contains": ["passport", "login"],
        "cookie_domain": ".baidu.com",
    },
    "sohu": {
        "name": "搜狐号",
        "url": "https://mp.sohu.com",
        "success_url_contains": ["mp.sohu.com"],
        "fail_url_contains": ["login"],
        "cookie_domain": ".sohu.com",
    },
}


def get_channel_cookie(channel: str) -> str | None:
    with get_session() as session:
        account = (
            session.query(ChannelAccount)
            .filter_by(channel=channel, is_active=True)
            .order_by(ChannelAccount.id.desc())
            .first()
        )
        if account is None or not account.credential:
            return None
        credential = account.credential
        expires_at = account.expires_at

    if expires_at is not None:
        now = datetime.datetime.now(datetime.UTC)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=datetime.UTC)
        if expires_at <= now:
            return None
    return decrypt_credential(credential)


def verify_channel(channel: str, cookie: str) -> dict:
    cfg = VERIFY_CONFIG[channel]
    from playwright.sync_api import sync_playwright

    result = {
        "channel": channel,
        "name": cfg["name"],
        "cookie_found": bool(cookie),
        "login_success": False,
        "url": "",
        "title": "",
        "editor_found": False,
        "screenshot": "",
        "error": "",
    }

    if not cookie:
        result["error"] = "数据库中无有效 Cookie"
        return result

    try:
        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": cfg["cookie_domain"],
                        "path": "/",
                        "secure": True,
                    }
                    for part in cookie.split(";")
                    if "=" in part
                    for name, value in [part.strip().split("=", 1)]
                    if name.strip()
                ]
            )
            page = context.new_page()
            page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            url = page.url
            title = page.title()
            result["url"] = url
            result["title"] = title

            # 判断是否登录成功
            success = all(part in url for part in cfg["success_url_contains"])
            failed = any(part in url for part in cfg["fail_url_contains"])
            result["login_success"] = success and not failed

            # 检测编辑器/发布按钮等关键元素
            has_editor = page.evaluate("""() => {
                return !!(
                    document.querySelector('[contenteditable="true"]') ||
                    document.querySelector('textarea') ||
                    document.querySelector('input[type="text"]') ||
                    document.querySelector('.public-DraftEditor-content')
                );
            }""")
            result["editor_found"] = bool(has_editor)

            # 截图
            ss_dir = get_temp_dir()
            ss_dir.mkdir(parents=True, exist_ok=True)
            ss_path = ss_dir / f"verify_{channel}.png"
            page.screenshot(path=str(ss_path), full_page=True)
            result["screenshot"] = str(ss_path)

            browser.close()
    except Exception as e:
        result["error"] = str(e)
        logger.exception("验证 %s 时出错", cfg["name"])

    return result


def print_result(r: dict):
    status = "[OK] 登录有效" if r["login_success"] else "[FAIL] 登录失效"
    editor = "[OK] 编辑器可访问" if r["editor_found"] else "[WARN] 未检测到编辑器"
    print(f"\n{'='*50}")
    print(f"渠道: {r['name']} ({r['channel']})")
    print(f"状态: {status}")
    print(f"编辑器: {editor}")
    print(f"当前 URL: {r['url']}")
    print(f"页面标题: {r['title']}")
    if r["screenshot"]:
        print(f"截图: {r['screenshot']}")
    if r["error"]:
        print(f"错误: {r['error']}")


def main():
    parser = argparse.ArgumentParser(description="验证各平台 Cookie 有效性")
    parser.add_argument(
        "channels",
        nargs="*",
        choices=list(VERIFY_CONFIG.keys()) + [[]],
        default=[],
        help="要验证的渠道（默认全部）",
    )
    args = parser.parse_args()

    channels = args.channels or list(VERIFY_CONFIG.keys())

    for ch in channels:
        logger.info("正在验证 %s…", VERIFY_CONFIG[ch]["name"])
        cookie = get_channel_cookie(ch)
        result = verify_channel(ch, cookie or "")
        print_result(result)


if __name__ == "__main__":
    main()
