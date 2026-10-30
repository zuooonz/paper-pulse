"""Playwright 浏览器自动化通用 Mixin

服务器 headless 方案（默认）：
- headless=True + Playwright 内置 Chromium
- 自定义 User-Agent + viewport + locale 绕过反爬
- 注入反检测脚本

本地调试方案（可选）：
- 设置环境变量 PLAYWRIGHT_HEADLESS=false
- channel="chrome" 使用系统 Chrome（指纹更真实）
"""
from __future__ import annotations
import logging
import os
import time
from abc import ABC
from pathlib import Path
from core.service_config import app_timezone

logger = logging.getLogger(__name__)


def parse_cookie_string(cookie_str: str, domain: str, extra_domains: list[str] | None = None) -> list[dict]:
    """将 'k1=v1; k2=v2' 解析为 Playwright add_cookies 可用的字典列表

    支持为关键域名额外注入 cookie（如小红书创作者中心需要 creator.xiaohongshu.com）
    """
    cookies = []
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        # 小红书需要 sameSite=None + secure 才能在创作者中心生效
        base = {
            "name": name,
            "value": value,
            "path": "/",
            "secure": True,
            "sameSite": "None",
        }
        cookies.append({**base, "domain": domain})
        # 为额外域名也注入（确保子域共享 cookie）
        for d in (extra_domains or []):
            cookies.append({**base, "domain": d})
    return cookies


def check_cookie_keys(cookie_str: str) -> dict:
    """检查 cookie 字符串中是否包含小红书关键登录字段"""
    keys = {}
    for part in (cookie_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            name = part.split("=", 1)[0].strip()
            keys[name] = True
    important = ["web_session", "a1", "webId", "xsecappid", "acw_tc"]
    return {k: keys.get(k, False) for k in important}


_STEALTH_INIT_SCRIPT = """
(function () {
    'use strict';
    // Safe defineProperty — 跳过 non-configurable 属性（新版 Chromium 中
    // navigator.webdriver 等属性已不可重定义，强行 defineProperty 会抛
    // TypeError 导致页面不稳定甚至崩溃）
    function safeDefine(obj, prop, descriptor) {
        try {
            var existing = Object.getOwnPropertyDescriptor(obj, prop);
            if (existing && !existing.configurable) return;
            Object.defineProperty(obj, prop, descriptor);
        } catch(e){}
    }

    // 1. navigator.webdriver
    safeDefine(navigator, 'webdriver', {get: function(){ return undefined; }});

    // 2. navigator.plugins
    safeDefine(navigator, 'plugins', {
        get: function() { return [
            {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1,item:function(i){return this[i];},namedItem:function(){return null;}},
            {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1,item:function(i){return this[i];},namedItem:function(){return null;}},
            {name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2,item:function(i){return this[i];},namedItem:function(){return null;}},
        ]; }
    });

    // 3. navigator.languages
    safeDefine(navigator, 'languages', {get: function(){ return ['zh-CN', 'zh', 'en-US', 'en']; }});

    // 4. window.chrome
    try {
        if (!window.chrome) {
            window.chrome = {runtime:{},loadTimes:function(){},csi:function(){},app:{}};
        }
    } catch(e){}

    // 5. permissions.query
    try {
        if (window.navigator.permissions && window.navigator.permissions.query) {
            var _origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = function(p) {
                if (p && p.name === 'notifications') return Promise.resolve({state: Notification.permission});
                return _origQuery.call(this, p);
            };
        }
    } catch(e){}

    // 6. iframe webdriver
    try {
        var f = document.createElement('iframe'); f.style.display='none';
        document.body.appendChild(f);
        safeDefine(f.contentWindow.navigator, 'webdriver', {get: function(){ return undefined; }});
        document.body.removeChild(f);
    } catch(e){}

    // 7. Notification
    try {
        if (window.Notification) safeDefine(Notification, 'permission', {get: function(){ return 'default'; }});
    } catch(e){}

    // 8. WebGL fingerprint override（Intel GPU）
    try {
        var _getParam = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Intel Open Source Technology Center';
            if (p === 37446) return 'Mesa DRI Intel(R) HD Graphics 620 (KBL GT2)';
            return _getParam.call(this, p);
        };
    } catch(e){}

    // 9. navigator.platform
    safeDefine(navigator, 'platform', {get: function(){ return 'Win32'; }});

    // 10. navigator.hardwareConcurrency
    safeDefine(navigator, 'hardwareConcurrency', {get: function(){ return 8; }});

    // 11. navigator.deviceMemory
    safeDefine(navigator, 'deviceMemory', {get: function(){ return 8; }});

    // 12. getBoundingClientRect 整数化（防指纹）
    try {
        var _origRect = Element.prototype.getBoundingClientRect;
        Element.prototype.getBoundingClientRect = function() {
            var r = _origRect.apply(this, arguments);
            try { r.width = Math.round(r.width); r.height = Math.round(r.height); } catch(e){}
            return r;
        };
    } catch(e){}

    // 13. mimeTypes
    safeDefine(navigator, 'mimeTypes', {get: function(){
        var mt = Object.create(MimeTypeArray.prototype);
        var types = [
            {type:'application/pdf',suffixes:'pdf',description:''},
            {type:'text/pdf',suffixes:'pdf',description:''},
        ];
        mt.length = types.length;
        types.forEach(function(t,i){mt[i]=t;});
        mt.item = function(i){return this[i];};
        mt.namedItem = function(n){return null;};
        return mt;
    }});
})();
"""


def _is_headless() -> bool:
    return os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() != "false"


def _random_chrome_version() -> str:
    """随机化 Chrome 版本号（120-125），用于更真实的 UA"""
    import random
    return str(120 + random.randint(0, 5))


def _get_user_agent() -> str:
    ver = _random_chrome_version()
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{ver}.0.0.0 Safari/537.36"
    )


def _get_browser_args(headless: bool) -> list[str]:
    """获取反检测浏览器启动参数（参考 helloxiaohong / puppeteer-extra-plugin-stealth）"""
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-size=1440,900",
        "--lang=zh-CN",
        "--no-sandbox",
        # 服务器 headless 稳定性：禁用 GPU 避免 SharedImageManager/GPU 崩溃
        "--disable-gpu",
        "--disable-software-rasterizer",
        # 减少内存占用（服务器常遇 OOM）
        "--disable-features=TranslateUI,Translate,site-per-process",
        "--js-flags=--max-old-space-size=512",
    ]
    return args


def _get_context_kwargs() -> dict:
    """获取反检测浏览器上下文参数（参考 helloxiaohong）"""
    return {
        "viewport": {"width": 1440, "height": 900},
        "user_agent": _get_user_agent(),
        "locale": "zh-CN",
        "timezone_id": app_timezone(),
        "geolocation": {"longitude": 121.4737, "latitude": 31.2304},
        "permissions": ["geolocation"],
        "color_scheme": "light",
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
        "java_script_enabled": True,
    }


def _launch_browser_with_channel_fallback(p, headless: bool, extra_args: list[str] | None = None):
    """启动浏览器，优先使用系统 Chrome（channel='chrome'），失败则回退到内置 Chromium"""
    args = _get_browser_args(headless)
    if extra_args:
        args = args + extra_args

    # 优先尝试系统 Chrome（指纹更真实，TLS/JA3 与真实浏览器一致）
    try:
        browser = p.chromium.launch(
            headless=headless,
            channel="chrome",
            args=args,
        )
        logger.info("Browser launched via channel=chrome (headless=%s)", headless)
        return browser
    except Exception as e:
        logger.warning("channel=chrome launch failed (%s), falling back to bundled Chromium", e)
        browser = p.chromium.launch(
            headless=headless,
            args=args,
        )
        logger.info("Browser launched via bundled Chromium (headless=%s)", headless)
        return browser


def launch_browser_context(p, *, inject_stealth: bool = True):
    """统一启动浏览器上下文（支持本地/服务器环境自动切换）

    inject_stealth: 是否注入反检测脚本。小红书页面可能反制过于激进的 stealth，
                    设为 False 可降低被检测风险（参考 xhs_ai_publisher）。
    """
    headless = _is_headless()
    browser = _launch_browser_with_channel_fallback(p, headless)
    ua = _get_user_agent()
    context_kwargs = _get_context_kwargs()
    context_kwargs["user_agent"] = ua
    context = browser.new_context(**context_kwargs)
    if inject_stealth:
        context.add_init_script(_STEALTH_INIT_SCRIPT)
        logger.info("Browser context created (headless=%s, ua=%s, stealth=on)", headless, ua[:60])
    else:
        logger.info("Browser context created (headless=%s, ua=%s, stealth=off)", headless, ua[:60])
    return browser, context


# ── 调试工具 ──────────────────────────────────────────────────────────

def _debug_dir() -> Path:
    d = Path(os.environ.get("PUBLISH_DEBUG_DIR", "tmp/publish_debug"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_screenshot(page, name: str) -> Path:
    """保存页面截图到调试目录，返回文件路径"""
    try:
        path = _debug_dir() / f"{name}_{int(time.time())}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info("Screenshot saved: %s", path)
        return path
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)
        return Path()


def save_page_html(page, name: str) -> Path:
    """保存页面 HTML 到调试目录，返回文件路径"""
    try:
        path = _debug_dir() / f"{name}_{int(time.time())}.html"
        html = page.content()
        path.write_text(html, encoding="utf-8")
        logger.info("HTML saved: %s", path)
        return path
    except Exception as e:
        logger.warning("HTML save failed: %s", e)
        return Path()


# ── 智能等待工具（供各 Publisher 使用）─────────────────────────────────

def wait_for_js_condition(page, condition_js: str, timeout_ms: int = 10000, poll_ms: int = 200) -> bool:
    """轮询等待 JS 表达式返回 truthy，满足后立即返回；超时返回 False。

    相比 page.wait_for_function，本函数在 JS 执行异常时不会抛错，
    更适合用于非关键条件的兜底检测。
    """
    import time
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            if page.evaluate(condition_js):
                return True
        except Exception:
            logger.debug('可选步骤失败，继续执行（wait_for_js_condition）', exc_info=True)
            pass
        page.wait_for_timeout(poll_ms)
    return False


def wait_for_any_selector(page, selectors: list[str], timeout_ms: int = 10000) -> str | None:
    """等待任一选择器匹配到可见元素，返回匹配到的选择器；超时返回 None。"""
    import time
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return sel
            except Exception:
                logger.debug('可选步骤失败，继续执行（wait_for_any_selector）', exc_info=True)
                pass
        page.wait_for_timeout(300)
    return None


# ── 编辑器图片上传通用辅助 ──────────────────────────────────────────────

def upload_image_to_editor(
    page,
    image_path: str,
    file_input_selectors: list[str] | None = None,
    toolbar_button_selectors: list[str] | None = None,
    post_upload_wait_ms: int = 3000,
) -> str | None:
    """
    通用辅助：尝试通过页面上的 file input 或工具栏按钮上传图片到编辑器。
    返回上传后的图片 URL（从页面 img 标签中提取），失败返回 None。

    策略顺序：
    1. 直接查找 file input 并 set_input_files
    2. 点击工具栏按钮触发 filechooser 后上传
    3. 兜底：页面内所有 input[type="file"] 逐个尝试
    """
    p = Path(image_path)
    if not p.exists():
        logger.warning("Image not found: %s", image_path)
        return None

    resolved = str(p.resolve())
    uploaded_url = None

    # 策略1: 通过指定选择器直接找 file input
    selectors = list(file_input_selectors or []) + [
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            # 确保可见/可操作
            loc.evaluate("el => { el.style.display='block'; el.style.visibility='visible'; el.style.opacity='1'; }")
            loc.set_input_files(resolved)
            logger.info("Set input files via selector: %s", sel)
            page.wait_for_timeout(post_upload_wait_ms)
            uploaded_url = _extract_last_image_url(page)
            if uploaded_url:
                logger.info("Uploaded image URL: %s", uploaded_url)
                return uploaded_url
        except Exception as e:
            logger.debug("Strategy1 selector %s failed: %s", sel, e)

    # 策略2: 点击工具栏按钮 + expect_file_chooser
    toolbar_selectors = list(toolbar_button_selectors or []) + [
        'button[title*="图片"]',
        'button[aria-label*="图片"]',
        'i[class*="image"]',
        'svg[class*="image"]',
        '[class*="toolbar"] [class*="image"]',
        '[class*="editor-tool"] [class*="image"]',
    ]
    for sel in toolbar_selectors:
        try:
            btn = page.locator(sel).first
            if btn.count() == 0 or not btn.is_visible():
                continue
            with page.expect_file_chooser(timeout=5000) as fc_info:
                btn.click(force=True)
            fc = fc_info.value
            fc.set_files(resolved)
            logger.info("Uploaded via toolbar button: %s", sel)
            page.wait_for_timeout(post_upload_wait_ms)
            uploaded_url = _extract_last_image_url(page)
            if uploaded_url:
                logger.info("Uploaded image URL: %s", uploaded_url)
                return uploaded_url
        except Exception as e:
            logger.debug("Strategy2 toolbar %s failed: %s", sel, e)

    # 策略3: 兜底 — 页面内所有 file input 逐个尝试
    try:
        inputs = page.locator('input[type="file"]').all()
        for inp in inputs:
            try:
                inp.evaluate("el => { el.style.display='block'; el.style.visibility='visible'; el.style.opacity='1'; }")
                inp.set_input_files(resolved)
                logger.info("Set input files via fallback input")
                page.wait_for_timeout(post_upload_wait_ms)
                uploaded_url = _extract_last_image_url(page)
                if uploaded_url:
                    logger.info("Uploaded image URL: %s", uploaded_url)
                    return uploaded_url
            except Exception:
                logger.debug('可选步骤失败，继续执行（upload_image_to_editor）', exc_info=True)
                continue
    except Exception as e:
        logger.debug("Strategy3 fallback failed: %s", e)

    logger.warning("All upload strategies failed for %s", image_path)
    return None


def paste_image_to_editor(page, image_path: str) -> str | None:
    """
    通过剪贴板粘贴事件上传图片到编辑器。
    原理：将本地图片转为 base64，在浏览器端构造 File + DataTransfer，
    dispatch paste 事件到编辑器，触发编辑器的粘贴上传逻辑。
    返回上传后的图片 URL，失败返回 None。
    """
    import base64

    p = Path(image_path)
    if not p.exists():
        logger.warning("Paste image: file not found: %s", image_path)
        return None

    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    ext = p.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime = mime_map.get(ext, "image/png")

    result = page.evaluate("""({b64, mime, name}) => {
        return new Promise((resolve) => {
            try {
                const byteCharacters = atob(b64);
                const byteNumbers = new Array(byteCharacters.length);
                for (let i = 0; i < byteCharacters.length; i++) {
                    byteNumbers[i] = byteCharacters.charCodeAt(i);
                }
                const byteArray = new Uint8Array(byteNumbers);
                const blob = new Blob([byteArray], {type: mime});
                const file = new File([blob], name, {type: mime});
                const dt = new DataTransfer();
                dt.items.add(file);

                // 优先聚焦已激活的元素
                let target = document.activeElement;
                if (!target || target === document.body) {
                    const selectors = [
                        '.monaco-editor textarea',
                        '.public-DraftEditor-content',
                        '.ql-editor',
                        '.ProseMirror',
                        '#ueditor_0',
                        '[contenteditable="true"]',
                        '.editor__inner',
                        'textarea',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) { target = el; break; }
                    }
                }
                if (!target) {
                    resolve({success: false, error: 'no editor target'});
                    return;
                }
                target.focus();
                target.click();

                const pasteEvent = new ClipboardEvent('paste', {
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true,
                });
                target.dispatchEvent(pasteEvent);
                resolve({success: true, target: target.className || target.tagName});
            } catch (e) {
                resolve({success: false, error: e.toString()});
            }
        });
    }""", {"b64": b64, "mime": mime, "name": p.name})

    if result and result.get("success"):
        logger.info("Pasted image to editor, target=%s", result.get("target"))
        page.wait_for_timeout(4000)
        return _extract_last_image_url(page)

    logger.warning("Paste image failed: %s", result.get("error") if result else "unknown")
    return None


def _extract_last_image_url(page) -> str | None:
    """从页面编辑器区域提取最后一张图片的 src URL。"""
    try:
        url = page.evaluate("""() => {
            // 优先从编辑器区域内找
            const editors = document.querySelectorAll(
                '.monaco-editor, .ql-editor, .public-DraftEditor-content, '
                '.ProseMirror, #ueditor_0, [contenteditable="true"]'
            );
            let imgs = [];
            if (editors.length > 0) {
                for (const ed of editors) {
                    imgs.push(...Array.from(ed.querySelectorAll('img')));
                }
            }
            // fallback: 全局搜索（排除头像、logo 等小图）
            if (imgs.length === 0) {
                imgs = Array.from(document.querySelectorAll('img')).filter(
                    img => img.width > 100 && img.height > 50
                );
            }
            if (imgs.length === 0) return null;
            // 返回最后一张（最新上传的）
            const last = imgs[imgs.length - 1];
            return last.src || null;
        }""")
        return url if isinstance(url, str) and url.startswith("http") else None
    except Exception:
        logger.debug('可选步骤失败，继续执行（_extract_last_image_url）', exc_info=True)
        return None


class PlaywrightBrowserMixin(ABC):
    """提供 Playwright + Chrome 的通用浏览器操作"""

    def _launch_browser(self, headless: bool = False):
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                channel="chrome",
                args=_get_browser_args(headless),
            )
            logger.info("Browser launched via channel=chrome (headless=%s)", headless)
        except Exception as e:
            logger.warning("channel=chrome failed (%s), using bundled Chromium", e)
            self._browser = self._playwright.chromium.launch(
                headless=headless,
                args=_get_browser_args(headless),
            )
            logger.info("Browser launched via bundled Chromium (headless=%s)", headless)
        ua = _get_user_agent()
        ctx_kwargs = _get_context_kwargs()
        ctx_kwargs["user_agent"] = ua
        self._context = self._browser.new_context(**ctx_kwargs)
        self._context.add_init_script(_STEALTH_INIT_SCRIPT)
        self._page = self._context.new_page()
        logger.info("Browser context ready (headless=%s, ua=%s)", headless, ua[:60])

    def _add_cookies(self, cookie_str: str, domain: str):
        cookies = parse_cookie_string(cookie_str, domain)
        self._context.add_cookies(cookies)
        logger.info("Added %d cookies for %s", len(cookies), domain)

    def _goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000):
        self._page.goto(url, wait_until=wait_until, timeout=timeout)

    def _wait(self, ms: int):
        self._page.wait_for_timeout(ms)

    def _screenshot(self, name: str) -> Path:
        path = Path(f"{name}_{int(time.time())}.png")
        self._page.screenshot(path=str(path))
        logger.info("Screenshot saved: %s", path)
        return path

    def _close_browser(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                logger.debug('可选步骤失败，继续执行（_close_browser）', exc_info=True)
                pass
        if getattr(self, "_playwright", None):
            try:
                self._playwright.stop()
            except Exception:
                logger.debug('可选步骤失败，继续执行（_close_browser）', exc_info=True)
                pass
        logger.info("Browser closed")
