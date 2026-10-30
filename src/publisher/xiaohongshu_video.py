"""小红书视频 publisher — Playwright (patchright) 浏览器自动化

技术路线：
- 复用推文发布器的 cookie 策略（.xiaohongshu.com + 子域注入）
- 使用 patchright.sync_api（与推文发布器一致）
- 视频上传：set_input_files 到 upload-input
- 标题/正文：ProseMirror/tiptap 编辑器，keyboard.type()
- 发布按钮：CustomEvent('publish') 策略（与推文发布器一致）

流程：上传页 → 上传视频 → 等待上传 → 填标题/描述/标签 →
      上传封面 → 原创声明 → dispatchEvent('publish') → 等待成功
"""
from __future__ import annotations
import logging
import os
import time
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import launch_browser_context, parse_cookie_string, save_screenshot
from tools import storage

logger = logging.getLogger(__name__)

# 小红书视频上传页
_UPLOAD_URL = "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video"


class XiaohongshuVideoPublisher(BasePublisher):
    channel_name = "xiaohongshu"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def publish_video(
        self,
        video_path: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        cover_path: str | None = None,
        check_cancel=None,
    ) -> dict:
        cookie = self.cookie
        if not cookie:
            return {"status": "failed", "error": "小红书 cookie 未配置"}

        try:
            self._publish_with_playwright(
                video_path, title, description, tags or [], cover_path,
                check_cancel=check_cancel, cookie=cookie,
            )
            return {"status": "success"}
        except Exception:
            logger.exception("[%s] 发布失败", self.channel_name)
            return {"status": "failed", "error": "视频发布失败，请稍后重试"}

    # ------------------------------------------------------------------
    # BasePublisher 兼容接口
    # ------------------------------------------------------------------
    def publish(
        self, draft_id: int, title: str, content: str,
        cover_images: Optional[List[str]] = None, **kwargs,
    ) -> PublishResult:
        cookie = self.cookie
        if not cookie:
            return PublishResult(success=False, channel=self.channel_name, error="小红书 cookie 未配置")

        try:
            cover_path = cover_images[0] if cover_images else None
            self._publish_with_playwright(
                video_path="", title=title, description=content,
                tags=[], cover_path=cover_path,
                check_cancel=kwargs.get("check_cancel"), cookie=cookie,
            )
            return PublishResult(success=True, channel=self.channel_name)
        except Exception:
            return PublishResult(success=False, channel=self.channel_name, error="视频发布失败，请稍后重试")

    # ------------------------------------------------------------------
    # Playwright 核心逻辑
    # ------------------------------------------------------------------
    def _publish_with_playwright(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list[str],
        cover_path: str | None,
        check_cancel=None,
        cookie: str = "",
    ):
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "视频发布需要浏览器自动化依赖，请安装 paper-pulse[browser]"
                ) from exc

        effective_cookie = cookie or self.cookie
        with sync_playwright() as p:
            browser, context = launch_browser_context(p, inject_stealth=True)

            # 注入 cookie（小红书需要多域注入）
            cookies = parse_cookie_string(
                effective_cookie, ".xiaohongshu.com",
                extra_domains=["creator.xiaohongshu.com", "www.xiaohongshu.com"],
            )
            context.add_cookies(cookies)
            page = context.new_page()

            try:
                # 1) 进入视频上传页
                logger.info("[%s] 进入小红书视频上传页...", self.channel_name)
                page.goto(_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                save_screenshot(page, "xhs_video_loaded")

                # 登录态检查
                if "login" in page.url.lower() or "signin" in page.url.lower():
                    browser.close()
                    raise RuntimeError("小红书 Cookie 已失效，请重新绑定账号")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 2) 上传视频
                logger.info("[%s] 上传视频: %s", self.channel_name, video_path)
                self._upload_video(page, video_path)
                save_screenshot(page, "xhs_video_uploaded")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 3) 等待上传完成
                logger.info("[%s] 等待上传完成...", self.channel_name)
                self._wait_for_upload(page)
                save_screenshot(page, "xhs_video_ready")

                # 4) 填写标题（最多 20 字符）
                logger.info("[%s] 填写标题...", self.channel_name)
                self._fill_title(page, title)
                save_screenshot(page, "xhs_video_title")

                # 5) 填写正文描述
                if description:
                    logger.info("[%s] 填写描述...", self.channel_name)
                    self._fill_description(page, description)
                    save_screenshot(page, "xhs_video_desc")

                # 6) 添加标签（最多 10 个）
                if tags:
                    logger.info("[%s] 添加标签...", self.channel_name)
                    self._add_tags(page, tags)
                    save_screenshot(page, "xhs_video_tags")

                # 7) 上传封面
                if cover_path:
                    logger.info("[%s] 上传封面...", self.channel_name)
                    self._upload_cover(page, cover_path)
                    save_screenshot(page, "xhs_video_cover")

                # 8) 原创声明
                logger.info("[%s] 原创声明...", self.channel_name)
                self._check_original(page)

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 9) 发布
                logger.info("[%s] 点击发布...", self.channel_name)
                self._click_publish(page)
                save_screenshot(page, "xhs_video_publishing")

                # 10) 等待成功（URL 跳转到 success 页面）
                try:
                    page.wait_for_url("**/publish/success?**", timeout=15000)
                    logger.info("[%s] 发布成功（URL 已跳转）", self.channel_name)
                except Exception:
                    # fallback: 检查 URL 是否包含 published=true
                    if "published=true" in page.url or "success" in page.url:
                        logger.info("[%s] 发布成功（URL 检测）", self.channel_name)
                    else:
                        logger.warning("[%s] 发布后 URL 未跳转: %s", self.channel_name, page.url)

                save_screenshot(page, "xhs_video_final")
                browser.close()

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass

    # ------------------------------------------------------------------
    # 页面操作封装
    # ------------------------------------------------------------------
    def _upload_video(self, page, video_path: str):
        """上传视频文件"""
        p = storage.resolve_storage_path(video_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 小红书视频上传入口
        for sel in [
            'div[class^="upload-content"] input[class="upload-input"]',
            'div[class^="upload-content"] input[type="file"]',
            'input[type="file"][accept*="video"]',
            'input[type="file"]',
        ]:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0:
                    inp.set_input_files(str(p.resolve()))
                    logger.info("[%s] 视频文件已上传 (%s)", self.channel_name, sel)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_upload_video）', exc_info=True)
                continue

        raise RuntimeError("小红书视频上传失败：找不到文件上传入口")

    def _wait_for_upload(self, page, timeout_sec: int = 300):
        """等待视频上传并转码完成"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                text = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
                # 完成标志：视频文件名出现 + 高清检测提示
                if "检测为高清视频" in text or "上传成功" in text:
                    logger.info("[%s] 视频上传完成", self.channel_name)
                    return
                # 上传中检测
                if "上传中" in text or "转码中" in text:
                    page.wait_for_timeout(3000)
                    continue
                # 标题输入框出现也表示上传完成
                if page.locator('input[placeholder*="填写标题"]').count() > 0:
                    logger.info("[%s] 视频上传完成（标题框已出现）", self.channel_name)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_wait_for_upload）', exc_info=True)
                pass
            page.wait_for_timeout(3000)
        logger.warning("[%s] 视频上传超时 (%ds)", self.channel_name, timeout_sec)

    def _fill_title(self, page, title: str):
        """填写标题（最多 20 字符）"""
        title = title[:20]
        for sel in ['input[placeholder*="填写标题"]', 'input.d-text', 'input[placeholder*="标题"]']:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    page.wait_for_timeout(200)
                    inp.fill(title)
                    # Vue 响应式唤醒
                    page.keyboard.press("Space")
                    page.keyboard.press("Backspace")
                    page.wait_for_timeout(300)
                    logger.info("[%s] 标题已填入 (%s)", self.channel_name, sel)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_title）', exc_info=True)
                continue

        # fallback: JS
        page.evaluate("""(title) => {
            const inputs = Array.from(document.querySelectorAll('input[placeholder]'));
            const target = inputs.find(i => (i.placeholder||'').includes('标题')) || inputs[0];
            if (target) {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(target, title);
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
                target.dispatchEvent(new Event('focus', { bubbles: true }));
                target.dispatchEvent(new Event('blur', { bubbles: true }));
            }
        }""", title)
        logger.info("[%s] 标题已填入 (JS fallback)", self.channel_name)

    def _fill_description(self, page, description: str):
        """填写描述（ProseMirror/tiptap 编辑器）"""
        description = description[:1000]
        try:
            editor = page.locator('p[data-placeholder*="输入正文描述"], div.tiptap.ProseMirror').first
            if editor.count() == 0:
                editor = page.locator('[contenteditable="true"]').first
            editor.click()
            page.wait_for_timeout(300)
            # ProseMirror 用 keyboard.type()
            page.keyboard.type(description, delay=5)
            logger.info("[%s] 描述已填入", self.channel_name)
            page.wait_for_timeout(500)
        except Exception as e:
            logger.warning("[%s] 描述填入失败: %s", self.channel_name, e)
            # fallback: JS
            page.evaluate("""(desc) => {
                const ed = document.querySelector('[contenteditable="true"], p[data-placeholder*="描述"]');
                if (ed) {
                    ed.innerHTML = '<p>' + desc.replace(/\\n/g, '</p><p>') + '</p>';
                    ed.dispatchEvent(new Event('input', { bubbles: true }));
                }
            }""", description)

    def _add_tags(self, page, tags: list[str]):
        """添加标签（#tagname 格式，最多 10 个）"""
        tags = [t.strip().lstrip("#") for t in tags[:10] if t.strip()]
        if not tags:
            return

        for tag in tags:
            try:
                # 在描述编辑器中输入 #tag
                editor = page.locator('p[data-placeholder*="输入正文描述"], div.tiptap.ProseMirror, [contenteditable="true"]').first
                editor.click()
                page.wait_for_timeout(200)
                page.keyboard.type(f" #{tag}")
                page.wait_for_timeout(1000)

                # 点击话题建议
                for sel in [
                    '#creator-editor-topic-container .item:first-child',
                    '[class*="topic"] [class*="item"]:first-child',
                    '[class*="tag"] [class*="item"]:first-child',
                ]:
                    try:
                        item = page.locator(sel).first
                        if item.count() > 0 and item.is_visible():
                            item.click(timeout=3000)
                            page.wait_for_timeout(300)
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_add_tags）', exc_info=True)
                        continue

                logger.info("[%s] 标签已添加: %s", self.channel_name, tag)
            except Exception as e:
                logger.warning("[%s] 标签添加失败: %s -> %s", self.channel_name, tag, e)
                continue

    def _upload_cover(self, page, cover_path: str):
        """上传自定义封面"""
        p = storage.resolve_storage_path(cover_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("[%s] 封面图不存在: %s", self.channel_name, cover_path)
            return

        try:
            # 点击封面区域
            cover_trigger = page.locator('div.cover > div.default, [class*="cover-plugin"] [class*="default"]').first
            if cover_trigger.count() > 0 and cover_trigger.is_visible():
                cover_trigger.click(force=True)
                page.wait_for_timeout(1500)

            # 在弹窗中上传
            for sel in [
                'div.d-modal input[type="file"][accept*="image"]',
                'div[class*="modal"] input[type="file"]',
                'input[type="file"][accept*="image"]',
            ]:
                try:
                    inp = page.locator(sel).first
                    if inp.count() > 0:
                        inp.set_input_files(str(p.resolve()))
                        logger.info("[%s] 封面图已上传 (%s)", self.channel_name, sel)
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    continue

            # 点击确定
            for btn_text in ["确定", "确认", "完成"]:
                try:
                    btn = page.get_by_role("button", name=btn_text)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click(timeout=3000)
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    continue

        except Exception as e:
            logger.warning("[%s] 封面上传失败: %s", self.channel_name, e)

    def _check_original(self, page):
        """勾选原创声明"""
        try:
            for sel in [
                'label:has-text("原创声明")',
                '[class*="original"] [class*="switch"]',
                'text=原创声明',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0 and el.is_visible():
                        el.click()
                        page.wait_for_timeout(500)
                        logger.info("[%s] 原创声明已勾选", self.channel_name)
                        return
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_check_original）', exc_info=True)
                    continue
        except Exception:
            logger.debug('可选步骤失败，继续执行（_check_original）', exc_info=True)
            pass

    def _click_publish(self, page):
        """点击发布按钮

        视频发布使用普通按钮点击（与图文发布的 CustomEvent 策略不同）。
        参考 social-auto-upload: page.locator('button:has-text("发布")').first.click()
        """
        page.wait_for_timeout(2000)

        # 策略1: button:has-text("发布")
        try:
            btn = page.locator('button:has-text("发布")').first
            if btn.count() > 0 and btn.is_visible():
                btn.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                btn.click(force=True, timeout=5000)
                logger.info("[%s] 发布按钮已点击 (button:has-text)", self.channel_name)
                return
        except Exception as e:
            logger.warning("[%s] button:has-text 发布按钮失败: %s", self.channel_name, e)

        # 策略2: get_by_role
        try:
            btn = page.get_by_role("button", name="发布", exact=True)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                btn.first.click(force=True, timeout=5000)
                logger.info("[%s] 发布按钮已点击 (get_by_role)", self.channel_name)
                return
        except Exception as e:
            logger.warning("[%s] get_by_role 发布按钮失败: %s", self.channel_name, e)

        # 策略3: CustomEvent fallback（与推文发布器一致）
        try:
            result = page.evaluate("""
                (() => {
                    const host = document.querySelector('xhs-publish-btn[is-publish="true"]');
                    if (!host) return 'not_found';
                    if (host.getAttribute('submit-disabled') === 'true') return 'disabled';
                    host.dispatchEvent(new CustomEvent('publish', { bubbles: true, cancelable: true }));
                    return 'fired';
                })()
            """)
            if result == "fired":
                logger.info("[%s] 发布事件已触发 (CustomEvent)", self.channel_name)
                return
            elif result == "disabled":
                raise RuntimeError("小红书发布按钮被禁用（内容不完整）")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[%s] CustomEvent 发布失败: %s", self.channel_name, e)

        # 策略4: JS 查找 div/span 中的"发布"
        try:
            result = page.evaluate("""() => {
                const allEls = Array.from(document.querySelectorAll('div, span, a'));
                const pub = allEls.find(el => {
                    return el.textContent.trim() === '发布' && el.offsetParent !== null;
                });
                if (pub) { pub.click(); return 'clicked'; }
                return 'not found';
            }""")
            if result == "clicked":
                logger.info("[%s] 发布按钮已点击 (JS)", self.channel_name)
                return
        except Exception as e:
            logger.warning("[%s] JS 发布按钮失败: %s", self.channel_name, e)

        raise RuntimeError("小红书未找到发布按钮")
