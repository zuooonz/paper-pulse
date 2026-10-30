"""Headless Playwright 自动登录模块

支持两种模式：
1. qrcode 模式：headless 提取登录页 QR 码，前端扫码后完成登录
2. sms 模式：headless 自动填写手机号发送验证码，用户输入验证码后完成登录

设计目标：
- 可在 headless Linux 服务器运行
- 支持多平台（知乎/CSDN/小红书/百家号/搜狐）
- 通过后端 API 异步驱动，前端轮询状态
"""
from __future__ import annotations

import base64
import datetime
import io
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
from core.service_config import app_timezone

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 平台配置
# ──────────────────────────────────────────────────────────────


class LoginMode(str, Enum):
    QRCODE = "qrcode"
    SMS = "sms"


@dataclass
class PlatformLoginConfig:
    name: str
    login_url: str
    # qrcode 模式
    qrcode_selector: str = ""
    # sms 模式选择器
    sms_tab_selector: str = ""           # 切换到短信登录标签
    phone_selector: str = ""             # 手机号输入框
    send_code_selector: str = ""         # 发送验证码按钮
    sms_code_selector: str = ""          # 验证码输入框
    sms_submit_selector: str = ""        # 短信登录提交按钮
    # 登录成功检测
    success_url_contains: list[str] = field(default_factory=list)
    success_url_excludes: list[str] = field(default_factory=list)
    # Cookie
    cookie_domain: str = ""
    required_cookies: list[str] = field(default_factory=list)
    # 额外等待时间（某些平台登录后跳转慢）
    post_login_wait: int = 3


PLATFORM_CONFIG: dict[str, PlatformLoginConfig] = {
    "zhihu": PlatformLoginConfig(
        name="知乎",
        login_url="https://www.zhihu.com/signin",
        qrcode_selector='canvas.Qrcode-qrcode, .Qrcode-qrcode',
        sms_tab_selector='text=验证码登录',
        phone_selector='input[name="phoneNo"], input[placeholder*="手机号"], input[type="tel"]',
        send_code_selector='button:has-text("获取短信验证码"), .SignFlow-smsInputButton',
        sms_code_selector='input[name="digits"], input[placeholder*="验证码"], input[type="number"]',
        sms_submit_selector='button[type="submit"], .SignFlow-submit',
        success_url_contains=["zhihu.com"],
        success_url_excludes=["signin", "login", "unhuman"],
        cookie_domain=".zhihu.com",
        required_cookies=["z_c0"],
        post_login_wait=5,
    ),
    "csdn": PlatformLoginConfig(
        name="CSDN",
        login_url="https://passport.csdn.net/login",
        qrcode_selector='.public-code, .login-code-wechat',
        sms_tab_selector='text=验证码登录',
        phone_selector='input[placeholder*="手机号"], input[placeholder*="手机"]',
        send_code_selector='button:has-text("获取验证码"), .send-code-btn',
        sms_code_selector='input[placeholder*="验证码"], input[placeholder*="code"]',
        sms_submit_selector='button:has-text("登录"), .login-btn',
        success_url_contains=["csdn.net"],
        success_url_excludes=["passport", "login"],
        cookie_domain=".csdn.net",
        post_login_wait=3,
    ),
    "xiaohongshu": PlatformLoginConfig(
        name="小红书",
        login_url="https://creator.xiaohongshu.com/login",
        qrcode_selector='.qrcode img, canvas',
        sms_tab_selector='text=短信登录, text=手机号登录',
        phone_selector='input[placeholder*="手机号"], input[type="tel"]',
        send_code_selector='.slot-right, div:has-text("发送验证码"), span:has-text("发送验证码"), [class*="typograhpy"]',
        sms_code_selector='input[placeholder*="验证码"]',
        sms_submit_selector='button:has-text("登录"), .beer-login-btn',
        success_url_contains=["creator.xiaohongshu.com"],
        success_url_excludes=["login"],
        cookie_domain=".xiaohongshu.com",
        post_login_wait=5,
    ),
    "baijiahao": PlatformLoginConfig(
        name="百家号",
        login_url="https://passport.baidu.com/v2/?login",
        qrcode_selector='.tang-pass-qrcode img, .tang-pass-qrcode-img',
        sms_tab_selector='text=短信登录, text=手机号登录',
        phone_selector='#TANGRAM__PSP_11__smsPhone, input[name="phone"], input[placeholder*="手机号"]',
        send_code_selector='#TANGRAM__PSP_11__smsTimer, button:has-text("获取验证码")',
        sms_code_selector='#TANGRAM__PSP_11__smsVerifyCode, input[placeholder*="验证码"]',
        sms_submit_selector='#TANGRAM__PSP_11__smsSubmit, button:has-text("登录")',
        success_url_contains=["baidu.com"],
        success_url_excludes=["passport", "login"],
        cookie_domain=".baidu.com",
        post_login_wait=5,
    ),
    "sohu": PlatformLoginConfig(
        name="搜狐号",
        login_url="https://mp.sohu.com/",
        qrcode_selector='.qrcode img',
        sms_tab_selector='text=手机登录, text=短信登录',
        phone_selector='input[placeholder*="手机号"], input[placeholder*="手机"]',
        send_code_selector='button:has-text("获取验证码")',
        sms_code_selector='input[placeholder*="验证码"]',
        sms_submit_selector='button:has-text("登录")',
        success_url_contains=["mp.sohu.com"],
        success_url_excludes=["login"],
        cookie_domain=".sohu.com",
        post_login_wait=3,
    ),
    "bilibili": PlatformLoginConfig(
        name="哔哩哔哩",
        login_url="https://passport.bilibili.com/login",
        qrcode_selector='.login_qrcode img, .qrcode-img, .login-scan__qrcode img',
        sms_tab_selector='text=短信登录, text=验证码登录',
        phone_selector='input[placeholder*="手机号"], input[type="tel"]',
        send_code_selector='button:has-text("获取验证码"), .btn-sms',
        sms_code_selector='input[placeholder*="验证码"], input[type="number"]',
        sms_submit_selector='button:has-text("登录"), button:has-text("确认"), .btn-login',
        success_url_contains=["bilibili.com"],
        success_url_excludes=["passport", "login"],
        cookie_domain=".bilibili.com",
        post_login_wait=5,
    ),
}


# ──────────────────────────────────────────────────────────────
# 反检测脚本
# ──────────────────────────────────────────────────────────────

_STEALTH_INIT_SCRIPT = """
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
    const navStart = (typeof performance !== 'undefined' && performance.timing)
        ? performance.timing.navigationStart : Date.now();
    if (typeof performance !== 'undefined') {
        performance.now = function () { return Date.now() - navStart; };
    }
    const _origToString = Function.prototype.toString;
    Function.prototype.toString = function () {
        if (this === Function.prototype.toString) return 'function toString() { [native code] }';
        return _origToString.apply(this, arguments);
    };
    try {
        let descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
        if (descriptor) {
            let _get = descriptor.get;
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                get: function () {
                    let win = _get.apply(this);
                    if (!win) return win;
                    return new Proxy(win, {
                        get: function (target, prop) {
                            const val = Reflect.get(target, prop, target);
                            return (typeof val === 'function') ? val.bind(target) : val;
                        }
                    });
                }
            });
        }
    } catch(e) {}
    if (typeof Notification !== 'undefined') {
        Object.defineProperty(Notification, 'permission', { get: () => 'default' });
    }
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
})();
"""


# ──────────────────────────────────────────────────────────────
# 任务状态管理
# ──────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    STARTED = "started"
    OPENING = "opening"
    WAITING_FOR_SCAN = "waiting_for_scan"
    WAITING_FOR_SMS_CODE = "waiting_for_sms_code"   # 已发送验证码，等待用户输入
    SUBMITTING = "submitting"
    LOGGED_IN = "logged_in"
    NEEDS_VERIFY = "needs_verify"                   # 扫码后需要二次验证
    EXTRACTING = "extracting"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class LoginTask:
    task_id: str
    channel: str
    mode: LoginMode
    status: TaskStatus = TaskStatus.PENDING
    message: str = ""
    qr_code_base64: Optional[str] = None
    cookie_str: Optional[str] = None
    account_name: str = ""
    phone: str = ""                                 # SMS 模式手机号
    sms_code: str = ""                              # 用户提交的验证码
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _sms_event: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self):
        self._cancel_event.set()
        self._sms_event.set()  # 唤醒可能正在等待 SMS 的线程
        self.status = TaskStatus.CANCELLED
        self.message = "用户取消"
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "channel": self.channel,
            "mode": self.mode.value,
            "status": self.status.value,
            "message": self.message,
            "qr_code_base64": self.qr_code_base64,
            "cookie_str": self.cookie_str[:200] + "..." if self.cookie_str and len(self.cookie_str) > 200 else self.cookie_str,
            "account_name": self.account_name,
            "phone": self.phone,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }


# 内存任务存储
_task_store: dict[str, LoginTask] = {}
_lock = threading.Lock()


def create_task(channel: str, mode: LoginMode, account_name: str = "", phone: str = "") -> LoginTask:
    task = LoginTask(
        task_id=str(uuid.uuid4()),
        channel=channel,
        mode=mode,
        account_name=account_name or f"{channel}_auto",
        phone=phone,
    )
    with _lock:
        _task_store[task.task_id] = task
    return task


def get_task(task_id: str) -> Optional[LoginTask]:
    with _lock:
        return _task_store.get(task_id)


def cleanup_old_tasks(max_age_seconds: float = 3600):
    now = time.time()
    with _lock:
        expired = [tid for tid, t in _task_store.items() if now - t.created_at > max_age_seconds]
        for tid in expired:
            del _task_store[tid]


# ──────────────────────────────────────────────────────────────
# 浏览器工具
# ──────────────────────────────────────────────────────────────

def _launch_browser_context(p, headless: bool = True, force_headed: bool = False):
    import os
    if os.environ.get("PLAYWRIGHT_HEADLESS", "").lower() == "false":
        headless = False
    if force_headed:
        headless = False
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }
    if not headless:
        launch_kwargs["channel"] = "chrome"
    browser = p.chromium.launch(**launch_kwargs)
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
    context.add_init_script(_STEALTH_INIT_SCRIPT)
    logger.info("Browser launched (headless=%s)", headless)
    return browser, context


def _is_logged_in(page, cfg: PlatformLoginConfig) -> bool:
    url = page.url
    for part in cfg.success_url_contains:
        if part not in url:
            return False
    for part in cfg.success_url_excludes:
        if part in url:
            return False
    return True


# ──────────────────────────────────────────────────────────────
# QR 码提取
# ──────────────────────────────────────────────────────────────

def _extract_qr_code(page, cfg: PlatformLoginConfig) -> Optional[str]:
    # 策略 1: 直接查找 img 标签的 src（selector 匹配到的元素或其内部 img）
    try:
        img_src = page.evaluate("""(selector) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const img = el.tagName === 'IMG' ? el : el.querySelector('img');
            if (img && img.src && img.src.startsWith('data:image')) {
                return img.src;
            }
            if (img && img.src) {
                return img.src;
            }
            return null;
        }""", cfg.qrcode_selector)
        if img_src:
            if img_src.startswith("data:image"):
                return img_src.split(",", 1)[1]
            try:
                resp = page.evaluate("""(url) => {
                    return fetch(url).then(r => r.blob()).then(b => {
                        return new Promise((resolve) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve(reader.result);
                            reader.readAsDataURL(b);
                        });
                    });
                }""", img_src)
                if resp and resp.startswith("data:image"):
                    return resp.split(",", 1)[1]
            except Exception:
                logger.debug('可选步骤失败，继续执行（_extract_qr_code）', exc_info=True)
                pass
    except Exception as e:
        logger.debug("QR strategy 1 (img src) failed: %s", e)

    # 策略 1b: 遍历页面所有 img，返回第一个较大的 base64 PNG（小红书 QR 是 base64 img）
    try:
        b64_img = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img'));
            for (const img of imgs) {
                if (img.src && img.src.startsWith('data:image/png;base64')) {
                    const rect = img.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) {
                        return img.src;
                    }
                }
            }
            return null;
        }""")
        if b64_img and b64_img.startswith("data:image"):
            return b64_img.split(",", 1)[1]
    except Exception as e:
        logger.debug("QR strategy 1b (base64 img) failed: %s", e)

    # 策略 2: 对 canvas 元素调用 toDataURL
    try:
        canvas_data = page.evaluate("""(selector) => {
            const el = document.querySelector(selector);
            if (!el) return null;
            const canvas = el.tagName === 'CANVAS' ? el : el.querySelector('canvas');
            if (canvas) {
                try { return canvas.toDataURL('image/png'); } catch(e) { return null; }
            }
            return null;
        }""", cfg.qrcode_selector)
        if canvas_data and canvas_data.startswith("data:image"):
            return canvas_data.split(",", 1)[1]
    except Exception as e:
        logger.debug("QR strategy 2 (canvas) failed: %s", e)

    # 策略 3: 对 QR 码区域截图
    try:
        if cfg.qrcode_selector:
            locator = page.locator(cfg.qrcode_selector).first
            if locator.count() > 0:
                screenshot_bytes = locator.screenshot()
                return base64.b64encode(screenshot_bytes).decode("ascii")
    except Exception as e:
        logger.debug("QR strategy 3 (screenshot) failed: %s", e)

    # 策略 4: 整页截图中裁剪（需要 Pillow）
    try:
        from PIL import Image
        full_bytes = page.screenshot()
        img = Image.open(io.BytesIO(full_bytes))
        w, h = img.size
        # 小红书登录框在右侧，裁剪右侧中间区域
        crop_box = (w * 0.65, h * 0.25, w * 0.90, h * 0.70)
        cropped = img.crop(crop_box)
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError:
        logger.debug("QR strategy 4 skipped: Pillow not installed")
    except Exception as e:
        logger.debug("QR strategy 4 (crop) failed: %s", e)

    return None


# ──────────────────────────────────────────────────────────────
# QR 码登录（含二次验证检测）
# ──────────────────────────────────────────────────────────────

def _check_needs_verification(page) -> bool:
    """检测扫码后是否需要二次验证（短信/验证码）。

    注意：只检测 URL 变化和明确的验证页面特征，避免将登录页本身的
    '验证码登录' 标签误判为二次验证。
    """
    try:
        url = page.url
        # 仅当 URL 明确跳转到验证/绑定页面时才判定为需要二次验证
        if any(k in url for k in ["verify", "captcha", "auth", "bind", "confirm"]):
            return True
        # 检测是否有图形验证码图片（canvas/img 带有 captcha/verify 类名或 src）
        has_captcha_img = page.evaluate("""() => {
            const imgs = Array.from(document.querySelectorAll('img, canvas'));
            return imgs.some(el => {
                const cls = (el.className || '').toLowerCase();
                const src = (el.src || '').toLowerCase();
                return cls.includes('captcha') || cls.includes('verify') || src.includes('captcha');
            });
        }""")
        if has_captcha_img:
            return True
        # 检测是否有明确的"需要验证"提示弹窗（通常有特定 class 的 modal）
        has_verify_modal = page.evaluate("""() => {
            const modals = document.querySelectorAll('.modal, .dialog, [role="dialog"], .verify-modal, .captcha-modal');
            return modals.length > 0 && Array.from(modals).some(m => m.offsetParent !== null);
        }""")
        return has_verify_modal
    except Exception:
        return False


def _do_qrcode_login(page, cfg: PlatformLoginConfig, task: LoginTask) -> bool:
    """QR 码登录：提取 QR 码，等待用户扫码。返回是否成功。"""
    task.status = TaskStatus.WAITING_FOR_SCAN
    task.message = "请使用对应 App 扫描屏幕上的二维码"
    task.updated_at = time.time()

    # 尝试切换到 QR 码标签
    qr_tab_selectors = [
        "text=扫码登录",
        "text=二维码登录",
        ".qrcode-tab",
        "[data-type='qrcode']",
        "img.css-wemwzq",           # 小红书右上角二维码切换图标
    ]
    for sel in qr_tab_selectors:
        try:
            qrcode_tab = page.locator(sel).first
            if qrcode_tab.count() > 0:
                qrcode_tab.click()
                time.sleep(0.8)
                break
        except Exception:
            logger.debug('可选步骤失败，继续执行（_do_qrcode_login）', exc_info=True)
            pass

    time.sleep(2)
    if task.is_cancelled():
        return False

    # 提取 QR 码
    qr_b64 = _extract_qr_code(page, cfg)
    if qr_b64:
        task.qr_code_base64 = qr_b64
        task.message = "二维码已生成，请在手机上扫码"
        logger.info("QR code extracted (%d bytes base64)", len(qr_b64))
    else:
        task.message = "无法自动提取二维码，请查看截图"
        logger.warning("Failed to extract QR code automatically")

    task.updated_at = time.time()

    # 轮询检测登录状态（最多 120 秒）
    for i in range(120):
        if task.is_cancelled():
            return False
        time.sleep(1)

        if _is_logged_in(page, cfg):
            return True

        # 检测是否触发二次验证（至少等待 5 秒后再检测，避免初始页面加载时误判）
        if i >= 5 and _check_needs_verification(page):
            task.status = TaskStatus.NEEDS_VERIFY
            task.message = "扫码成功，但平台要求二次验证（如短信验证），请手动完成验证后继续"
            task.updated_at = time.time()
            logger.info("QR login requires secondary verification")
            # 继续轮询，给用户时间完成验证
            for j in range(60):
                if task.is_cancelled():
                    return False
                time.sleep(1)
                if _is_logged_in(page, cfg):
                    return True
                if j % 10 == 0:
                    task.message = f"等待完成二次验证... 已等待 {j} 秒"
                    task.updated_at = time.time()
            task.error = "二次验证超时，请重试"
            return False

        if i % 10 == 0:
            task.message = f"等待扫码中... 已等待 {i} 秒"
            task.updated_at = time.time()

    task.error = "二维码登录超时（120秒），请重试"
    return False


# ──────────────────────────────────────────────────────────────
# SMS 验证码登录（两阶段）
# ──────────────────────────────────────────────────────────────

def _detect_captcha_blocking(page) -> str | None:
    """检测页面上是否有可见的验证码/安全验证组件阻止了 SMS 发送。

    支持检测：多种验证码和安全验证组件、
    通用图形验证码、滑块验证、安全验证弹窗等。
    返回检测到的类型描述，无则返回 None。
    """
    # 1. 网易易盾 (yidun) — 知乎常用
    has_yidun = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.yidun_bg-img, .yidun_panel, .yidun_modal, [class*="yidun_"]'))
            .some(el => el.offsetParent !== null);
    }""")
    if has_yidun:
        # 进一步判断类型
        is_click = page.evaluate("""() => {
            const body = document.body.innerText || '';
            return body.includes('请依次点击') || body.includes('请完成安全验证');
        }""")
        if is_click:
            return "网易易盾文字点选验证"
        return "网易易盾安全验证"

    # 2. 极验 (geetest)
    has_geetest = page.evaluate("""() => {
        const el = document.querySelector('.geetest_challenge, .geetest_box, .geetest_panel, .geetest_widget');
        return el !== null && el.offsetParent !== null;
    }""")
    if has_geetest:
        return "极验验证码"

    # 3. 通用滑块验证码
    has_slider_vendor = page.evaluate("""() => {
        const el = document.querySelector('.nc-container, .nc_wrapper, .nc-mask, .nc-lang-cnt');
        return el !== null && el.offsetParent !== null;
    }""")
    if has_slider_vendor:
        return "滑块验证码"

    # 4. 腾讯防水墙
    has_tencent = page.evaluate("""() => {
        const el = document.querySelector('#tcaptcha, .tcaptcha, .t-captcha, .taptcha-container');
        return el !== null && el.offsetParent !== null;
    }""")
    if has_tencent:
        return "腾讯防水墙验证码"

    # 5. 通用图形验证码
    has_captcha = page.evaluate("""() => {
        const imgs = Array.from(document.querySelectorAll('img, canvas'));
        return imgs.some(el => {
            const cls = (el.className || '').toLowerCase();
            const src = (el.src || '').toLowerCase();
            return cls.includes('captcha') || src.includes('captcha') || cls.includes('verify');
        });
    }""")
    if has_captcha:
        return "图形验证码"

    # 6. 通用滑块验证
    has_slider = page.evaluate("""() => {
        const sliders = document.querySelectorAll('.slider, .slide-verify, [class*="slide-verify"], [class*="slider-verify"]');
        return sliders.length > 0 && Array.from(sliders).some(el => el.offsetParent !== null);
    }""")
    if has_slider:
        return "滑块验证"

    # 7. 安全验证/弹窗（通用兜底）
    has_security_modal = page.evaluate("""() => {
        const bodyText = document.body.innerText || '';
        if (bodyText.includes('请完成安全验证') || bodyText.includes('请依次点击') || bodyText.includes('安全校验')) return true;
        const modals = document.querySelectorAll('[class*="modal"], [class*="dialog"], [role="dialog"], [class*="popup"], [class*="overlay"]');
        return Array.from(modals).some(m => {
            const style = window.getComputedStyle(m);
            return m.offsetParent !== null && (style.position === 'fixed' || style.position === 'absolute') && parseInt(style.zIndex) > 100;
        });
    }""")
    if has_security_modal:
        return "安全验证弹窗"

    return None


def _do_sms_login_phase1(page, cfg: PlatformLoginConfig, task: LoginTask) -> bool:
    """SMS 第一阶段：填写手机号并发送验证码。返回是否成功触发发送。"""
    if not task.phone:
        task.error = "手机号为空"
        return False

    # 尝试切换到 SMS 登录标签
    if cfg.sms_tab_selector:
        tab_selectors = cfg.sms_tab_selector.split(", ")
        for sel in tab_selectors:
            try:
                tab = page.locator(sel.strip()).first
                if tab.count() > 0:
                    tab.click()
                    time.sleep(1)
                    logger.info("Clicked SMS tab: %s", sel)
                    break
            except Exception:
                logger.debug('可选步骤失败，继续执行（_do_sms_login_phase1）', exc_info=True)
                pass

    time.sleep(1)
    if task.is_cancelled():
        return False

    # 填写手机号
    try:
        page.wait_for_selector(cfg.phone_selector, timeout=10000)
        page.locator(cfg.phone_selector).first.fill(task.phone)
        logger.info("Filled phone number")
    except Exception as e:
        task.error = f"无法找到手机号输入框: {e}"
        return False

    time.sleep(0.5)
    if task.is_cancelled():
        return False

    # 点击发送验证码（Playwright locator + JS fallback）
    clicked = False
    for sel in (cfg.send_code_selector or "").split(", "):
        try:
            loc = page.locator(sel.strip()).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(force=True)
                clicked = True
                logger.info("Clicked send code button via locator: %s", sel)
                break
        except Exception:
            logger.debug('可选步骤失败，继续执行（_do_sms_login_phase1）', exc_info=True)
            pass
    if not clicked:
        # JS fallback: 遍历所有可见元素找包含"发送验证码"文本的
        try:
            js_clicked = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('div, span, button, a'));
                const target = all.find(el => {
                    const text = el.textContent.trim();
                    const rect = el.getBoundingClientRect();
                    return (text.includes('发送验证码') || text.includes('获取验证码'))
                        && rect.width > 0 && rect.height > 0
                        && window.getComputedStyle(el).display !== 'none';
                });
                if (target) { target.click(); return true; }
                return false;
            }""")
            if js_clicked:
                clicked = True
                logger.info("Clicked send code button via JS fallback")
        except Exception:
            logger.debug('可选步骤失败，继续执行（_do_sms_login_phase1）', exc_info=True)
            pass
    if not clicked:
        task.error = "无法点击发送验证码按钮"
        return False

    # 等待 3 秒，让验证码/弹窗有足够时间出现
    time.sleep(3)

    # ── 综合验证码/安全验证检测 ──
    captcha_info = _detect_captcha_blocking(page)
    if captcha_info:
        task.error = f"平台触发「{captcha_info}」，自动登录无法完成，请使用二维码登录方式"
        return False

    # 检测错误提示文本
    error_text = page.evaluate("""() => {
        const body = document.body.innerText || '';
        const patterns = ['过于频繁', '请稍后', '格式错误', '无效的手机号', '请输入正确的手机号', '发送失败', '操作频繁'];
        for (const p of patterns) {
            if (body.includes(p)) return p;
        }
        return null;
    }""")
    if error_text:
        task.error = f"发送验证码失败: {error_text}，请使用二维码登录方式"
        return False

    # 检测发送按钮是否进入倒计时状态（说明点击生效，但验证码被拦截）
    btn_after = page.locator(cfg.send_code_selector).first
    if btn_after.count() > 0:
        btn_text = btn_after.inner_text()
        if any(c in btn_text for c in "0123456789") and "秒" in btn_text:
            # 按钮进入了倒计时，说明请求已发出或被拦截后进入了冷却
            # 继续流程，但如果用户长时间没收到短信，可能是被验证码拦截了
            logger.info("Send button entered cooldown: %s", btn_text)

    task.status = TaskStatus.WAITING_FOR_SMS_CODE
    task.message = "验证码已发送，请输入收到的短信验证码"
    task.updated_at = time.time()
    return True


def _do_xiaohongshu_sms_login(page, task: LoginTask) -> bool:
    """小红书专用 SMS 登录第一阶段（页面结构特殊：div 按钮、默认短信登录）。"""
    if not task.phone:
        task.error = "手机号为空"
        return False

    # 小红书默认就是短信登录，无需切换标签
    # 1) 填写手机号（区号默认 +86，无需处理）
    try:
        phone_input = page.locator('input[placeholder="手机号"]').first
        if phone_input.count() == 0:
            # fallback: 找 type=tel 或包含手机关键字的 input
            phone_input = page.locator('input[type="tel"], input[placeholder*="手机"]').first
        if phone_input.count() > 0:
            phone_input.fill(task.phone)
            logger.info("Xiaohongshu phone filled")
        else:
            task.error = "未找到手机号输入框"
            return False
    except Exception as e:
        task.error = f"填写手机号失败: {e}"
        return False

    time.sleep(0.5)
    if task.is_cancelled():
        return False

    # 2) 点击发送验证码（小红书是 div 按钮，不是 button）
    clicked = False
    try:
        # 策略 A: 找包含"发送验证码"文本的可见元素
        send_btn = page.locator('[class*="typograhpy"]').filter(has_text="发送验证码").first
        if send_btn.count() > 0 and send_btn.is_visible():
            send_btn.click(force=True)
            clicked = True
            logger.info("Xiaohongshu send-code clicked (typograhpy)")
    except Exception:
        logger.debug('可选步骤失败，继续执行（_do_xiaohongshu_sms_login）', exc_info=True)
        pass

    if not clicked:
        try:
            # 策略 B: JS 遍历找文本
            js_ok = page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('div, span'));
                const btn = all.find(el => {
                    const t = el.textContent.trim();
                    const rect = el.getBoundingClientRect();
                    return (t === '发送验证码' || t === '获取验证码')
                        && rect.width > 0 && rect.height > 0
                        && window.getComputedStyle(el).display !== 'none';
                });
                if (btn) { btn.click(); return true; }
                return false;
            }""")
            if js_ok:
                clicked = True
                logger.info("Xiaohongshu send-code clicked (JS)")
        except Exception:
            logger.debug('可选步骤失败，继续执行（_do_xiaohongshu_sms_login）', exc_info=True)
            pass

    if not clicked:
        task.error = "无法点击发送验证码按钮"
        return False

    time.sleep(3)

    # 3) 检测安全验证拦截
    captcha_info = _detect_captcha_blocking(page)
    if captcha_info:
        task.error = f"平台触发「{captcha_info}」，请使用二维码登录"
        return False

    # 4) 检测错误提示
    error_text = page.evaluate("""() => {
        const body = document.body.innerText || '';
        const patterns = ['过于频繁', '请稍后', '格式错误', '无效的手机号', '发送失败', '操作频繁'];
        for (const p of patterns) {
            if (body.includes(p)) return p;
        }
        return null;
    }""")
    if error_text:
        task.error = f"发送验证码失败: {error_text}"
        return False

    task.status = TaskStatus.WAITING_FOR_SMS_CODE
    task.message = "验证码已发送，请输入收到的短信验证码"
    task.updated_at = time.time()
    return True


def _do_sms_login_phase2(page, cfg: PlatformLoginConfig, task: LoginTask) -> bool:
    """SMS 第二阶段：填写验证码并提交登录。"""
    if not task.sms_code:
        task.error = "验证码为空"
        return False

    # 填写验证码
    try:
        page.wait_for_selector(cfg.sms_code_selector, timeout=5000)
        page.locator(cfg.sms_code_selector).first.fill(task.sms_code)
        logger.info("Filled SMS code")
    except Exception as e:
        task.error = f"无法找到验证码输入框: {e}"
        return False

    time.sleep(0.5)
    if task.is_cancelled():
        return False

    # 提交登录
    task.status = TaskStatus.SUBMITTING
    task.message = "正在提交验证码..."
    task.updated_at = time.time()

    try:
        page.locator(cfg.sms_submit_selector).first.click()
        logger.info("Clicked SMS submit")
    except Exception as e:
        # 尝试按回车提交
        try:
            page.locator(cfg.sms_code_selector).first.press("Enter")
            logger.info("Pressed Enter to submit")
        except Exception:
            task.error = f"无法点击提交按钮: {e}"
            return False

    # 等待登录结果
    for i in range(30):
        if task.is_cancelled():
            return False
        time.sleep(1)
        if _is_logged_in(page, cfg):
            return True
        url = page.url
        if "captcha" in url or "verify" in url:
            task.message = "需要额外验证，请尝试 QR 码登录模式"
            task.error = "平台要求额外验证，SMS 登录失败"
            return False

    task.error = "登录超时，请检查验证码或尝试 QR 码登录"
    return False


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def run_login_task(
    task: LoginTask,
    on_status_change: Optional[Callable[[LoginTask], None]] = None,
):
    """在后台线程中执行登录任务。"""
    from playwright.sync_api import sync_playwright

    cfg = PLATFORM_CONFIG.get(task.channel)
    if not cfg:
        task.status = TaskStatus.FAILED
        task.error = f"不支持的渠道: {task.channel}"
        task.updated_at = time.time()
        if on_status_change:
            on_status_change(task)
        return

    def _notify():
        if on_status_change:
            try:
                on_status_change(task)
            except Exception:
                logger.debug('可选步骤失败，继续执行（_notify）', exc_info=True)
                pass

    browser = None
    page = None
    try:
        with sync_playwright() as p:
            task.status = TaskStatus.STARTED
            task.message = "正在启动浏览器..."
            task.updated_at = time.time()
            _notify()

            # 小红书 SMS 登录容易被 headless 检测拦截，强制使用 headed 模式
            use_headed = (task.channel == "xiaohongshu" and task.mode == LoginMode.SMS)
            browser, context = _launch_browser_context(p, headless=True, force_headed=use_headed)
            page = context.new_page()

            task.status = TaskStatus.OPENING
            task.message = f"正在打开 {cfg.name} 登录页..."
            task.updated_at = time.time()
            _notify()

            page.goto(cfg.login_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            if task.is_cancelled():
                browser.close()
                return

            # 检查是否已经是登录状态
            if _is_logged_in(page, cfg):
                task.status = TaskStatus.LOGGED_IN
                task.message = "检测到已登录状态"
                task.updated_at = time.time()
                _notify()
            else:
                if task.mode == LoginMode.SMS:
                    # SMS 第一阶段：发送验证码（小红书结构特殊，走专用方法）
                    if task.channel == "xiaohongshu":
                        phase1_ok = _do_xiaohongshu_sms_login(page, task)
                    else:
                        phase1_ok = _do_sms_login_phase1(page, cfg, task)
                    if not phase1_ok:
                        task.status = TaskStatus.FAILED
                        task.updated_at = time.time()
                        _notify()
                        browser.close()
                        return
                    _notify()

                    # 等待用户输入验证码（最多 5 分钟）
                    logger.info("Waiting for user to input SMS code (task=%s)", task.task_id)
                    task._sms_event.wait(timeout=300)

                    if task.is_cancelled():
                        browser.close()
                        return

                    if not task.sms_code:
                        task.status = TaskStatus.FAILED
                        task.error = "未收到验证码输入，已超时"
                        task.updated_at = time.time()
                        _notify()
                        browser.close()
                        return

                    # SMS 第二阶段：提交验证码
                    phase2_ok = _do_sms_login_phase2(page, cfg, task)
                    if not phase2_ok:
                        task.status = TaskStatus.FAILED
                        task.updated_at = time.time()
                        _notify()
                        browser.close()
                        return
                else:
                    # QR 码模式
                    success = _do_qrcode_login(page, cfg, task)
                    if not success:
                        task.status = TaskStatus.FAILED
                        task.updated_at = time.time()
                        _notify()
                        browser.close()
                        return

                task.status = TaskStatus.LOGGED_IN
                task.message = "登录成功！"
                task.updated_at = time.time()
                _notify()

            # 提取 cookie
            task.status = TaskStatus.EXTRACTING
            task.message = "正在提取登录态..."
            task.updated_at = time.time()
            _notify()

            time.sleep(cfg.post_login_wait)
            cookies = context.cookies()
            from tools.cookie_utils import format_cookie_string

            cookie_str = format_cookie_string(cookies)

            missing = [req for req in cfg.required_cookies if req not in cookie_str]
            if missing:
                logger.warning("Cookie 缺少关键字段: %s", missing)

            task.cookie_str = cookie_str
            task.status = TaskStatus.SUCCESS
            task.message = f"登录态提取成功（{len(cookies)} 条 cookie）"
            task.updated_at = time.time()
            _notify()

            browser.close()

    except Exception as e:
        logger.exception("Login task failed")
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.updated_at = time.time()
        _notify()
        if browser:
            try:
                browser.close()
            except Exception:
                logger.debug('可选步骤失败，继续执行（run_login_task）', exc_info=True)
                pass


def start_login_task(
    channel: str,
    mode: LoginMode,
    account_name: str = "",
    phone: str = "",
    on_status_change: Optional[Callable[[LoginTask], None]] = None,
) -> LoginTask:
    """创建并启动一个登录任务（在后台线程中运行）。"""
    cleanup_old_tasks()
    task = create_task(channel, mode, account_name, phone)

    thread = threading.Thread(
        target=run_login_task,
        args=(task, on_status_change),
        daemon=True,
    )
    task._thread = thread
    thread.start()
    return task


def submit_sms_code_to_task(task_id: str, sms_code: str) -> LoginTask | None:
    """用户提交短信验证码，唤醒等待中的任务。"""
    task = get_task(task_id)
    if not task:
        return None
    if task.status != TaskStatus.WAITING_FOR_SMS_CODE:
        return None
    task.sms_code = sms_code
    task._sms_event.set()
    return task


# ──────────────────────────────────────────────────────────────
# 保存到数据库
# ──────────────────────────────────────────────────────────────

def save_cookie_to_db(channel: str, account_name: str, cookie_str: str, note: str = "") -> dict:
    from paper_library.db import get_session
    from paper_library.models import ChannelAccount
    from publisher.credential_store import encrypt_credential

    TTL_MAP = {
        "csdn": 30,
        "zhihu": 30,
        "baijiahao": 14,
        "xiaohongshu": 7,
        "sohu": 30,
        "bilibili": 30,
    }
    now = datetime.datetime.now(datetime.UTC)
    expires_at = now + datetime.timedelta(days=TTL_MAP.get(channel, 30))

    with get_session(".", "config") as session:
        acc = ChannelAccount(
            channel=channel,
            account_name=account_name or f"{channel}_auto",
            credential_type="cookie",
            credential=encrypt_credential(cookie_str),
            expires_at=expires_at,
            is_active=True,
            bound_at=now,
            note=note or f"Playwright auto-login @ {now.isoformat()}",
        )
        session.add(acc)
        session.flush()
        return {
            "id": acc.id,
            "channel": acc.channel,
            "account_name": acc.account_name,
            "expires_at": acc.expires_at.isoformat() if acc.expires_at else None,
            "is_active": acc.is_active,
            "note": acc.note,
        }
