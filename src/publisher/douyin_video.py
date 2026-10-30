"""抖音视频 publisher — Playwright 浏览器自动化

技术路线：
- Playwright sync API + Cookie 注入登录态
- 参考 social-auto-upload 的 douyin_uploader/main.py 实现
- 复用 _browser_mixin 的 stealth 脚本和浏览器启动

流程：上传页 → 上传视频 → 等待上传 → 填标题/描述/标签 →
      自主声明 → 上传封面 → 点击发布 → 等待成功
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

# 抖音上传页
_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
# 发布后页面
_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"


class DouyinVideoPublisher(BasePublisher):
    channel_name = "douyin"

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
            return {"status": "failed", "error": "抖音 cookie 未配置"}

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
            return PublishResult(success=False, channel=self.channel_name, error="抖音 cookie 未配置")

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
        from playwright.sync_api import sync_playwright

        effective_cookie = cookie or self.cookie
        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(effective_cookie, ".douyin.com"))
            page = context.new_page()

            try:
                # 1) 进入上传页
                logger.info("[%s] 进入抖音上传页...", self.channel_name)
                page.goto(_UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(3000)
                save_screenshot(page, "douyin_video_loaded")

                # 登录态检查
                if "login" in page.url.lower() or "passport" in page.url.lower():
                    browser.close()
                    raise RuntimeError("抖音 Cookie 已失效，请重新绑定账号")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 2) 上传视频文件
                logger.info("[%s] 上传视频: %s", self.channel_name, video_path)
                self._upload_video(page, video_path)
                save_screenshot(page, "douyin_video_uploaded")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 3) 等待上传完成
                logger.info("[%s] 等待上传完成...", self.channel_name)
                self._wait_for_upload(page)
                save_screenshot(page, "douyin_video_ready")

                # 4) 填写标题（最多 30 字符）
                logger.info("[%s] 填写标题...", self.channel_name)
                self._fill_title(page, title)
                save_screenshot(page, "douyin_video_title")

                # 5) 填写描述/标签
                if description or tags:
                    logger.info("[%s] 填写描述/标签...", self.channel_name)
                    self._fill_description_and_tags(page, description, tags)
                    save_screenshot(page, "douyin_video_desc")

                # 6) 自主声明 — 选择"无需声明"
                logger.info("[%s] 自主声明...", self.channel_name)
                self._set_self_declaration(page)

                # 7) 上传封面
                if cover_path:
                    logger.info("[%s] 上传封面...", self.channel_name)
                    self._upload_cover(page, cover_path)
                    save_screenshot(page, "douyin_video_cover")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 8) 点击发布
                logger.info("[%s] 点击发布...", self.channel_name)
                self._click_publish(page)
                save_screenshot(page, "douyin_video_publishing")

                # 9) 等待成功
                page.wait_for_timeout(5000)
                save_screenshot(page, "douyin_video_final")

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

        # 等待上传入口挂载
        try:
            page.locator("div[class^='container'] input").wait_for(state="attached", timeout=60000)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_upload_video）', exc_info=True)
            pass

        # 上传视频
        uploaded = False
        for sel in [
            "div[class^='container'] input",
            'input[type="file"][accept*="video"]',
            'input[type="file"]',
        ]:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0:
                    inp.set_input_files(str(p.resolve()))
                    uploaded = True
                    logger.info("[%s] 视频文件已上传 (%s)", self.channel_name, sel)
                    break
            except Exception:
                logger.debug('可选步骤失败，继续执行（_upload_video）', exc_info=True)
                continue

        if not uploaded:
            raise RuntimeError("抖音视频上传失败：找不到文件上传入口")

    def _wait_for_upload(self, page, timeout_sec: int = 300):
        """等待视频上传并转码完成"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                text = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
                # 上传完成标志
                if "重新上传" in text:
                    logger.info("[%s] 视频上传完成", self.channel_name)
                    return
                # 上传失败
                if "上传失败" in text:
                    raise RuntimeError("抖音视频上传失败")
            except RuntimeError:
                raise
            except Exception:
                logger.debug('可选步骤失败，继续执行（_wait_for_upload）', exc_info=True)
                pass
            page.wait_for_timeout(3000)
        logger.warning("[%s] 视频上传超时 (%ds)", self.channel_name, timeout_sec)

    def _fill_title(self, page, title: str):
        """填写标题（最多 30 字符）"""
        title = title[:30]
        # 等待标题输入框出现（上传完成后才会显示）
        try:
            page.locator('input[placeholder*="填写作品标题"]').wait_for(state="visible", timeout=120000)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_fill_title）', exc_info=True)
            pass

        for sel in [
            'input[placeholder*="填写作品标题"]',
            'input[placeholder*="标题"]',
        ]:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    page.wait_for_timeout(300)
                    inp.fill(title)
                    logger.info("[%s] 标题已填入 (%s)", self.channel_name, sel)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_title）', exc_info=True)
                continue

        # fallback
        page.evaluate("""(title) => {
            const inputs = Array.from(document.querySelectorAll('input[placeholder]'));
            const target = inputs.find(i => (i.placeholder||'').includes('标题'));
            if (target) {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(target, title);
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", title)
        logger.info("[%s] 标题已填入 (JS fallback)", self.channel_name)

    def _fill_description_and_tags(self, page, description: str, tags: list[str]):
        """填写描述和标签（在 contenteditable 区域中）"""
        # 找到描述编辑器
        try:
            editor = page.locator('div.zone-container[contenteditable="true"], div[contenteditable="true"]').first
            if editor.count() == 0:
                editor = page.locator('[contenteditable="true"]').first
            editor.click()
            page.wait_for_timeout(300)

            # 清空现有内容
            page.keyboard.press("Control+a")
            page.wait_for_timeout(100)
            page.keyboard.press("Backspace")
            page.wait_for_timeout(200)

            # 先输入描述
            if description:
                page.keyboard.type(description[:500], delay=5)
                page.wait_for_timeout(300)

            # 输入标签（#tagname 格式）
            for tag in tags[:5]:
                tag = tag.strip().lstrip("#")
                if tag:
                    page.keyboard.type(f" #{tag}")
                    page.wait_for_timeout(500)
                    # 尝试选择话题建议
                    try:
                        item = page.locator('[class*="topic"] [class*="item"]:first-child, [class*="mention"] [class*="item"]:first-child').first
                        if item.count() > 0 and item.is_visible():
                            item.click(timeout=2000)
                            page.wait_for_timeout(300)
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_fill_description_and_tags）', exc_info=True)
                        pass

            # 按 Escape 关闭话题下拉
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            logger.info("[%s] 描述和标签已填入", self.channel_name)
        except Exception as e:
            logger.warning("[%s] 描述/标签填入失败: %s", self.channel_name, e)

    def _set_self_declaration(self, page):
        """选择自主声明 → 无需声明"""
        try:
            # 找到"创作声明"区域
            for sel in [
                '.form-label:has-text("创作声明")',
                'text=创作声明',
            ]:
                try:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        # 点击下拉框
                        dropdown = page.locator('input[placeholder*="创作"], .form-inner-wrap input').first
                        if dropdown.count() > 0:
                            dropdown.click()
                            page.wait_for_timeout(2000)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_set_self_declaration）', exc_info=True)
                    continue

            # 点击"无需声明"
            page.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('*'));
                const target = items.find(el => el.textContent.trim() === '无需声明' && el.children.length === 0);
                if (target) {
                    const parent = target.parentElement;
                    if (parent) parent.click();
                }
            }""")
            page.wait_for_timeout(1000)
            logger.info("[%s] 自主声明已设置", self.channel_name)
        except Exception as e:
            logger.warning("[%s] 自主声明设置失败: %s", self.channel_name, e)

    def _upload_cover(self, page, cover_path: str):
        """上传自定义封面"""
        p = storage.resolve_storage_path(cover_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("[%s] 封面图不存在: %s", self.channel_name, cover_path)
            return

        try:
            # 找到封面上传入口
            for sel in [
                'input.semi-upload-hidden-input',
                'input[type="file"][accept*="image"]',
                'input[type="file"]',
            ]:
                try:
                    inp = page.locator(sel).first
                    if inp.count() > 0:
                        inp.set_input_files(str(p.resolve()))
                        logger.info("[%s] 封面图已上传 (%s)", self.channel_name, sel)
                        page.wait_for_timeout(3000)
                        return
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    continue

            logger.warning("[%s] 封面上传失败", self.channel_name)
        except Exception as e:
            logger.warning("[%s] 封面上传异常: %s", self.channel_name, e)

    def _click_publish(self, page):
        """点击发布按钮"""
        page.wait_for_timeout(2000)

        # 移除新手引导等干扰元素
        page.evaluate("""() => {
            document.querySelectorAll('.shepherd-element, .shepherd-modal-overlay-container, [class*="mention-wrapper"]').forEach(e => e.remove());
        }""")

        for attempt in range(5):
            try:
                publish_btn = page.get_by_role("button", name="发布", exact=True)
                if publish_btn.count() > 0 and publish_btn.first.is_visible():
                    publish_btn.first.click(force=True)
                    # 等待 URL 跳转到管理页
                    try:
                        page.wait_for_url("**/content/manage**", timeout=5000)
                        logger.info("[%s] 发布成功（URL已跳转）", self.channel_name)
                        return
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                        pass
                    logger.info("[%s] 发布按钮已点击 (attempt %d)", self.channel_name, attempt + 1)
                    page.wait_for_timeout(2000)
                    continue
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                pass

            # fallback: JS 点击
            try:
                page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if (b.textContent.trim() === '发布') { b.click(); return; }
                    }
                }""")
                page.wait_for_timeout(3000)
                if "manage" in page.url:
                    logger.info("[%s] 发布成功（JS点击+URL跳转）", self.channel_name)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                pass

        # 最后检查 URL
        if "manage" in page.url:
            logger.info("[%s] 发布成功（URL已跳转）", self.channel_name)
            return

        logger.warning("[%s] 发布按钮多次点击未成功", self.channel_name)
        save_screenshot(page, "douyin_video_publish_fail")
        raise RuntimeError("抖音发布按钮点击失败")
