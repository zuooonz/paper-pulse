"""
Multi-platform video publisher.
Supports: Baijiahao (Playwright), Bilibili (Playwright),
          Xiaohongshu (patchright/Playwright), Douyin (Playwright).
"""
import json, logging, time
from tools.storage import get_temp_dir

logger = logging.getLogger(__name__)

URLS = {"baijiahao":"https://baijiahao.baidu.com/builder/rc/edit?type=videoV2"}

# 平台约束
PLATFORM_CONSTRAINTS = {
    "baijiahao": {"label":"百家号", "max_duration_sec":900, "max_size_mb":1024, "formats":["mp4","mov"]},
    "bilibili":  {"label":"哔哩哔哩", "max_duration_sec":3600, "max_size_mb":8192, "formats":["mp4","flv"]},
    "xiaohongshu": {"label":"小红书", "max_duration_sec":300, "max_size_mb":2048, "formats":["mp4","mov"]},
    "douyin":    {"label":"抖音", "max_duration_sec":900, "max_size_mb":4096, "formats":["mp4","mov"]},
}


def get_supported_platforms():
    return [{"id": k, **v} for k, v in PLATFORM_CONSTRAINTS.items()]


def _get_publisher(channel: str, cookie: str):
    """根据渠道名实例化对应的视频 publisher"""
    channel = channel.lower()
    if channel == "baijiahao":
        # 内联实现（保持向后兼容）
        return None  # 由原有逻辑处理
    elif channel == "bilibili":
        from publisher.bilibili_video import BilibiliVideoPublisher
        return BilibiliVideoPublisher(cookie=cookie)
    elif channel == "xiaohongshu":
        from publisher.xiaohongshu_video import XiaohongshuVideoPublisher
        return XiaohongshuVideoPublisher(cookie=cookie)
    elif channel == "douyin":
        from publisher.douyin_video import DouyinVideoPublisher
        return DouyinVideoPublisher(cookie=cookie)
    return None

def _cookies_file(raw, domain):
    c = [{"name":p.split("=",1)[0].strip(),"value":p.split("=",1)[1].strip(),"domain":domain,"path":"/"}
         for p in (raw or "").split(";") if "=" in p]
    path = get_temp_dir() / f"bjh_{hash(raw)&0xffff}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies":c,"origins":[]}))
    return str(path)

def publish_video(video_path, title, description, tags, account_cookie, platform,
                  account_name="", cover_path=None, headless=True):
    platform = platform.lower()

    # 新平台：通过 publisher 类处理
    pub = _get_publisher(platform, account_cookie or "")
    if pub is not None:
        return pub.publish_video(
            video_path=video_path, title=title,
            description=description or "", tags=tags or [],
            cover_path=cover_path,
        )

    # 百家号：原有逻辑
    if platform not in URLS:
        return {"status":"failed","error":f"Unknown: {platform}"}
    try: from playwright.sync_api import sync_playwright
    except ImportError: return {"status":"failed","error":"No Playwright"}

    try:
        sp = _cookies_file(account_cookie or "", ".baidu.com")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(storage_state=sp, viewport={"width":1280,"height":800})
            page = ctx.new_page()
            page.goto(URLS[platform], wait_until="domcontentloaded", timeout=60000)
            try: page.wait_for_load_state("networkidle", timeout=10000)
            except: pass
            time.sleep(3)

            if "login" in page.url:
                browser.close()
                return {"status":"failed","error":"Cookie expired"}

            result = _bjh_publish(page, video_path, title)
            browser.close()
            return result
    except Exception:
        return {"status":"failed","error":"视频发布失败，请稍后重试"}


def _bjh_publish(page, video_path, title):
    """Baijiahao video publish. Uses toast-based success detection (like tweet publisher)."""

    # 1. Upload video
    page.locator('input[type="file"]').first.set_input_files(video_path)
    try: page.wait_for_selector("div#formMain:visible", timeout=60000)
    except: return {"status":"failed","error":"Form timeout"}

    # Wait for upload complete
    for _ in range(90):
        if page.locator('div .cover-overlay:has-text("上传失败")').count():
            return {"status":"failed","error":"Upload failed"}
        if page.locator('div .cover-overlay:has-text("上传中")').count():
            time.sleep(2); continue
        break
    for _ in range(40):
        if page.locator("div.cheetah-spin-container img").count(): break
        time.sleep(2)

    # 2. Fill title using keyboard.type (same as tweet publisher)
    tf = page.locator('[contenteditable="true"]').first
    if tf.count():
        tf.click()
        # Select all
        page.keyboard.press("Control+a"); time.sleep(0.1)
        page.keyboard.press("Backspace")
        page.keyboard.type(title[:30])
        time.sleep(0.5)

    # 3. Select 创作声明 → 无需声明 (click input + click option)
    try: page.wait_for_selector('.form-label:has-text("创作声明")', timeout=30000)
    except: pass
    decl = page.locator('input[placeholder*="创作"]').first
    if not decl.count():
        decl = page.locator('.form-label:has-text("创作声明") + .form-inner-wrap input').first
    if decl.count():
        decl.click(); time.sleep(2)
        # Click 无需声明 via JS (need to hit React parent click handler)
        page.evaluate("""() => {
            document.querySelectorAll('*').forEach(el => {
                if (el.textContent.trim() === '无需声明' && el.children.length === 0) {
                    const p = el.parentElement;
                    if (p) { p.click(); }
                }
            });
        }""")
        time.sleep(0.5)

    # 4. Close popups (same as tweet publisher)
    for _ in range(2):
        try:
            page.evaluate("""() => {
                const texts = ['我知道了', '确定', '关闭', '取消', '暂不', '跳过', '下一步'];
                const btns = document.querySelectorAll('button, span, div, a');
                for (const t of texts) {
                    for (const b of btns) {
                        if (b.textContent && b.textContent.trim() === t) { b.click(); }
                    }
                }
                document.querySelectorAll('.cheetah-modal-close, [class*="close"], [class*="modal-mask"], [class*="overlay"]').forEach(el => { el.click(); });
            }""")
            time.sleep(0.5)
        except: pass

    # 5. Click publish — multiple strategies (same as tweet publisher)
    publish_clicked = False
    strategies = [
        'button:has-text("发布"):not(:has-text("定时"))',
        'button[class*="publish"]',
        '[data-testid="publish-btn"]',
    ]
    for sel in strategies:
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click(force=True, timeout=5000)
                publish_clicked = True
                break
        except: pass

    if not publish_clicked:
        # JS fallback (same as tweet publisher)
        page.evaluate("""() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                if (b.textContent && b.textContent.includes('发布') && !b.textContent.includes('定时')) {
                    b.click(); return;
                }
            }
        }""")
    time.sleep(3)

    # Handle post-publish dialog (like tweet publisher)
    try:
        page.evaluate("""() => {
            const texts = ['保持视频发布','确定','确认','我知道了','继续发布','同意并发布'];
            const btns = document.querySelectorAll('button');
            for (const t of texts) {
                for (const b of btns) {
                    if (b.textContent && b.textContent.includes(t)) { b.click(); return; }
                }
            }
        }""")
        time.sleep(1)
    except: pass

    # 6. Detect success via toast/notification (AJAX-based, same as tweet publisher)
    #    Baijiahao video publishing is AJAX — page doesn't redirect
    time.sleep(3)
    try:
        toast_text = page.evaluate("""() => {
            const selectors = ['.ant-message', '.ant-notification', '[class*="toast"]', '[class*="notification"]', '[class*="msg"]', '[class*="hint"]'];
            for (const s of selectors) {
                const el = document.querySelector(s);
                if (el && el.textContent.trim()) return el.textContent.trim();
            }
            return '';
        }""")
    except:
        toast_text = ""

    page_text = page.evaluate("() => document.body ? (document.body.innerText || '') : ''") or ""

    success_keywords = ["审核中", "等待审核", "提交成功", "发布成功", "内容已提交", "已提交审核"]

    if any(kw in page_text for kw in success_keywords) or any(kw in toast_text for kw in success_keywords):
        return {"status": "success", "url": page.url, "message": "Published (toast confirmed)"}

    error_keywords = ["发布失败", "审核未通过", "内容违规", "请修改后重试", "包含违规内容", "请添加封面", "标题不能为空", "请选择活动", "请添加地点", "请选择创作"]
    for kw in error_keywords:
        if kw in page_text:
            return {"status": "failed", "error": f"Baijiahao: {kw}"}

    # If no toast and no error, check if URL changed
    if "builder/rc/clue" in page.url or "builder/rc/edit" not in page.url:
        return {"status": "success", "url": page.url, "message": "Published (URL changed)"}

    return {"status": "failed", "error": "Publish not confirmed. Toast: " + (toast_text[:80] or "none")}
