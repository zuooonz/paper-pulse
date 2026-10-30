"""B站视频 publisher — Playwright 浏览器自动化

技术路线：
- Playwright sync API + Cookie 注入登录态
- 复用 _browser_mixin 的 stealth 脚本和浏览器启动
- 视频上传使用 set_input_files，复用百家号 video_publisher 的策略
- 标题/简介/标签使用 keyboard.type() 模拟输入

流程：上传页 → 上传视频 → 等待上传完成 → 填标题/简介/标签 →
      上传封面 → 点击发布
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

# B站视频上传页
_UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"


class BilibiliVideoPublisher(BasePublisher):
    channel_name = "bilibili"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    # ------------------------------------------------------------------
    # 公开接口（与 video_publisher.py 对齐）
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
        """发布视频到 B站。返回 {"status": "success"|"failed", "url": ..., "error": ...}"""
        cookie = self.cookie
        if not cookie:
            return {"status": "failed", "error": "B站 cookie 未配置"}

        try:
            self._publish_with_playwright(
                video_path, title, description, tags or [], cover_path,
                check_cancel=check_cancel, cookie=cookie,
            )
            return {"status": "success"}
        except Exception as e:
            logger.error("[%s] 发布失败: %s", self.channel_name, e)
            return {"status": "failed", "error": "视频发布失败，请稍后重试"}

    # ------------------------------------------------------------------
    # BasePublisher 接口（保留，供统一调度用）
    # ------------------------------------------------------------------
    def publish(
        self, draft_id: int, title: str, content: str,
        cover_images: Optional[List[str]] = None, **kwargs,
    ) -> PublishResult:
        """兼容 BasePublisher 接口：content 字段作为 description"""
        cookie = self.cookie
        if not cookie:
            return PublishResult(success=False, channel=self.channel_name, error="B站 cookie 未配置")

        try:
            cover_path = cover_images[0] if cover_images else None
            self._publish_with_playwright(
                video_path="", title=title, description=content,
                tags=[], cover_path=cover_path,
                check_cancel=kwargs.get("check_cancel"), cookie=cookie,
            )
            return PublishResult(success=True, channel=self.channel_name)
        except Exception as e:
            logger.error("[%s] 发布失败: %s", self.channel_name, e)
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
            context.add_cookies(parse_cookie_string(effective_cookie, ".bilibili.com"))
            page = context.new_page()

            try:
                # 1) 进入上传页验证登录态
                logger.info("[%s] 进入B站视频上传页...", self.channel_name)
                page.goto(_UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # 关闭通知权限弹窗（"member.bilibili.com想要显示通知"）
                page.evaluate("""() => {
                    // 点击"禁止"按钮关闭通知弹窗
                    const btns = Array.from(document.querySelectorAll('button'));
                    const block = btns.find(b => b.textContent.trim() === '禁止');
                    if (block) block.click();
                    // 关闭引导气泡
                    document.querySelectorAll('[class*="guide"], [class*="tooltip"], [class*="popover"]').forEach(e => {
                        const close = e.querySelector('[class*="close"], .anticon-close, svg');
                        if (close) close.click();
                    });
                }""")
                page.wait_for_timeout(1000)

                save_screenshot(page, "bilibili_video_loaded")

                if "passport" in page.url.lower() or "login" in page.url.lower():
                    browser.close()
                    raise RuntimeError("B站 Cookie 已失效，请重新绑定账号")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 2) 上传视频文件
                logger.info("[%s] 上传视频文件: %s", self.channel_name, video_path)
                self._upload_video(page, video_path)
                save_screenshot(page, "bilibili_video_uploaded")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 3) 等待上传完成并填写元数据
                logger.info("[%s] 等待上传完成...", self.channel_name)
                self._wait_for_upload_complete(page)
                save_screenshot(page, "bilibili_video_ready")

                # 4) 填写标题
                logger.info("[%s] 填写标题...", self.channel_name)
                self._fill_title(page, title)
                save_screenshot(page, "bilibili_video_title")

                # 5) 填写简介
                if description:
                    logger.info("[%s] 填写简介...", self.channel_name)
                    self._fill_description(page, description)
                    save_screenshot(page, "bilibili_video_desc")

                # 6) 添加标签
                if tags:
                    logger.info("[%s] 添加标签...", self.channel_name)
                    self._add_tags(page, tags)
                    save_screenshot(page, "bilibili_video_tags")

                # 7) 上传封面
                if cover_path:
                    logger.info("[%s] 上传封面...", self.channel_name)
                    self._upload_cover(page, cover_path)
                    save_screenshot(page, "bilibili_video_cover")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return

                # 8) 选择创作声明（B站必填项）
                logger.info("[%s] 选择创作声明...", self.channel_name)
                self._set_creation_declaration(page)

                # 9) 点击发布
                logger.info("[%s] 点击发布...", self.channel_name)
                self._click_publish(page)
                save_screenshot(page, "bilibili_video_published")

                # 等待发布完成
                page.wait_for_timeout(5000)

                # 检查是否发布成功
                if "success" in page.url or "content/manage" in page.url:
                    logger.info("[%s] 发布成功（URL 已跳转）", self.channel_name)
                else:
                    # 检查是否有错误提示
                    error_text = page.evaluate("""() => {
                        const els = document.querySelectorAll('.error, [class*="error"], [class*="toast"]');
                        for (const el of els) {
                            if (el.textContent.trim()) return el.textContent.trim();
                        }
                        return '';
                    }""")
                    if error_text:
                        logger.warning("[%s] 发布可能有误: %s", self.channel_name, error_text)

                save_screenshot(page, "bilibili_video_final")
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
        """上传视频文件到B站"""
        p = storage.resolve_storage_path(video_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # B站上传页使用 div 包裹的隐藏 file input
        # 策略：先尝试直接 set_input_files
        uploaded = False
        for sel in [
            'div.b-upload-video input[type="file"]',
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
            # fallback: 通过拖拽区域上传
            page.evaluate("""() => {
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'video/*';
                input.style.display = 'none';
                document.body.appendChild(input);
                input.click();
            }""")
            page.wait_for_timeout(1000)
            # 尝试 set_input_files on any file input
            try:
                inp = page.locator('input[type="file"]').first
                if inp.count() > 0:
                    inp.set_input_files(str(p.resolve()))
                    uploaded = True
            except Exception:
                logger.debug('可选步骤失败，继续执行（_upload_video）', exc_info=True)
                pass

        if not uploaded:
            raise RuntimeError("B站视频上传失败：找不到文件上传入口")

    def _wait_for_upload_complete(self, page, timeout_sec: int = 300):
        """等待视频上传并转码完成"""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                text = page.evaluate("() => document.body ? (document.body.innerText || '') : ''")
                # 转码完成标志
                if "上传完成" in text or "编辑" in text or "预览" in text:
                    logger.info("[%s] 视频上传完成", self.channel_name)
                    return
                # 上传中检测
                if "上传中" in text or "转码中" in text:
                    page.wait_for_timeout(3000)
                    continue
                # 上传失败检测
                if "上传失败" in text or "重新上传" in text:
                    raise RuntimeError("B站视频上传失败")
                # URL 变化也视为上传完成
                if "frame" not in page.url.lower() and "upload" not in page.url.lower():
                    return
            except RuntimeError:
                raise
            except Exception:
                logger.debug('可选步骤失败，继续执行（_wait_for_upload_complete）', exc_info=True)
                pass
            page.wait_for_timeout(3000)
        logger.warning("[%s] 视频上传超时 (%ds)", self.channel_name, timeout_sec)

    def _fill_title(self, page, title: str):
        """填写标题（B站视频标题最多 80 字符）"""
        title = title[:80]
        # B站视频标题：input 在 "标题" 标签旁边，可能已有文件名
        for sel in [
            'input[placeholder*="标题"]',
            'input[name="title"]',
            '[class*="title"] input',
            # B站视频投稿页标题输入框（通过 aria 或 data 属性）
            'input[maxlength="80"]',
            'input[maxlength="100"]',
        ]:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    page.wait_for_timeout(300)
                    # 全选清空后填入
                    page.keyboard.press("Control+a")
                    page.wait_for_timeout(100)
                    inp.fill(title)
                    logger.info("[%s] 标题已填入 (%s)", self.channel_name, sel)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_title）', exc_info=True)
                continue

        # fallback: JS
        page.evaluate("""(title) => {
            const inputs = Array.from(document.querySelectorAll('input'));
            // 找标题输入框：maxlength 80/100，或者旁边有"标题"文字
            let target = null;
            for (const inp of inputs) {
                const ml = inp.getAttribute('maxlength');
                if (ml === '80' || ml === '100') { target = inp; break; }
                // 通过 label 关联
                const prev = inp.parentElement;
                if (prev && prev.textContent.includes('标题')) { target = inp; break; }
            }
            if (!target) target = inputs.find(i => (i.placeholder||'').includes('标题'));
            if (target) {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                nativeSetter.call(target, title);
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", title)
        logger.info("[%s] 标题已填入 (JS fallback)", self.channel_name)

    def _fill_description(self, page, description: str):
        """填写简介（B站用 textarea，最多 2000 字符）"""
        description = description[:2000]
        for sel in [
            'textarea[placeholder*="简介"]',
            'textarea[placeholder*="描述"]',
            'textarea[placeholder*="填写"]',
            'textarea[maxlength="2000"]',
            '[class*="desc"] textarea',
        ]:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0:
                    inp.scroll_into_view_if_needed()
                    page.wait_for_timeout(300)
                    inp.click()
                    page.wait_for_timeout(300)
                    inp.fill(description)
                    logger.info("[%s] 简介已填入 (%s)", self.channel_name, sel)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_description）', exc_info=True)
                continue

        # fallback: JS
        page.evaluate("""(desc) => {
            const tas = Array.from(document.querySelectorAll('textarea'));
            // 找简介 textarea（maxlength 2000 或 placeholder 含简介）
            let target = null;
            for (const ta of tas) {
                const ml = ta.getAttribute('maxlength');
                if (ml === '2000') { target = ta; break; }
                if ((ta.placeholder||'').includes('简介') || (ta.placeholder||'').includes('描述')) {
                    target = ta; break;
                }
            }
            if (!target && tas.length > 0) target = tas[0];
            if (target) {
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                nativeSetter.call(target, desc);
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", description)
        logger.info("[%s] 简介已填入 (JS fallback)", self.channel_name)

    def _add_tags(self, page, tags: list[str]):
        """添加标签（最多 12 个）"""
        tags = [t.strip() for t in tags[:12] if t.strip()]
        if not tags:
            return

        for tag in tags:
            try:
                # 找到标签输入框
                inp = page.locator('input[placeholder*="标签"]').first
                if inp.count() == 0:
                    inp = page.locator('[class*="tag"] input').first
                if inp.count() == 0:
                    break

                inp.click()
                page.wait_for_timeout(300)
                inp.fill(tag)
                page.wait_for_timeout(500)

                # 按回车或选择建议项
                for sel in [
                    '[class*="tag-suggestion"] [class*="item"]:first-child',
                    '[class*="tag-list"] [class*="item"]:first-child',
                    '.tag-item:first-child',
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
                else:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(300)

                logger.info("[%s] 标签已添加: %s", self.channel_name, tag)
            except Exception as e:
                logger.warning("[%s] 标签添加失败: %s -> %s", self.channel_name, tag, e)
                continue

    def _upload_cover(self, page, cover_path: str):
        """上传自定义封面图"""
        p = storage.resolve_storage_path(cover_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("[%s] 封面图不存在: %s", self.channel_name, cover_path)
            return

        try:
            # 找到封面上传区域
            for sel in [
                '[class*="cover"] [class*="upload"]',
                '[class*="cover"] input[type="file"]',
                'input[type="file"][accept*="image"]',
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

    def _set_creation_declaration(self, page):
        """选择创作声明（B站必填项）"""
        try:
            # 点击创作声明下拉框
            decl_dropdown = page.locator('[class*="declaration"] select, [class*="creative"] select, select[placeholder*="创作声明"]').first
            if decl_dropdown.count() == 0:
                # fallback: 通过文本定位
                decl_dropdown = page.locator('text=请选择符合您视频内容的创作声明').first
            if decl_dropdown.count() > 0:
                decl_dropdown.click()
                page.wait_for_timeout(1000)

            # 选择"内容无需标注"
            for option_text in ["内容无需标注", "无需标注", "自制"]:
                try:
                    option = page.locator(f'text={option_text}').first
                    if option.count() > 0 and option.is_visible():
                        option.click()
                        page.wait_for_timeout(500)
                        logger.info("[%s] 创作声明已选择: %s", self.channel_name, option_text)
                        return
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_set_creation_declaration）', exc_info=True)
                    continue

            # fallback: JS 选择
            page.evaluate("""() => {
                const options = Array.from(document.querySelectorAll('li, div[role="option"], .option'));
                const target = options.find(el => el.textContent.includes('无需标注') || el.textContent.includes('自制'));
                if (target) target.click();
            }""")
            page.wait_for_timeout(500)
            logger.info("[%s] 创作声明已选择 (JS fallback)", self.channel_name)
        except Exception as e:
            logger.warning("[%s] 创作声明设置失败: %s", self.channel_name, e)

    def _click_publish(self, page):
        """点击发布按钮（B站视频用"立即投稿"，注意是 div 不是 button！）"""
        page.wait_for_timeout(2000)

        # 滚动到底部让"立即投稿"可见
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

        # 策略1: JS 查找 div/span 中的"立即投稿"（B站用 div 渲染按钮）
        try:
            result = page.evaluate("""() => {
                // 优先找 class 含 submit 的元素
                const allEls = Array.from(document.querySelectorAll('div, span, a'));
                const submitBtn = allEls.find(el => {
                    const cls = el.className || '';
                    const text = el.textContent.trim();
                    return text === '立即投稿' && (cls.includes('submit') || cls.includes('btn'));
                });
                if (submitBtn) { submitBtn.click(); return 'clicked: 立即投稿 (submit class)'; }

                // fallback: 找文本精确匹配的可见 div/span
                const exact = allEls.find(el => {
                    return el.textContent.trim() === '立即投稿' && el.offsetParent !== null;
                });
                if (exact) { exact.click(); return 'clicked: 立即投稿 (exact text)'; }

                return 'not found';
            }""")
            logger.info("[%s] JS 投稿按钮结果: %s", self.channel_name, result)
            if "clicked" in result:
                return
        except Exception as e:
            logger.warning("[%s] JS 投稿按钮失败: %s", self.channel_name, e)

        # 策略2: Playwright text 定位
        for btn_text in ["立即投稿", "发布", "投稿"]:
            try:
                btn = page.get_by_text(btn_text, exact=True)
                if btn.count() > 0:
                    btn.first.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    btn.first.click(force=True, timeout=5000)
                    logger.info("[%s] 投稿按钮已点击 (get_by_text '%s')", self.channel_name, btn_text)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                continue

        # 策略3: get_by_role button
        for btn_text in ["立即投稿", "发布", "投稿"]:
            try:
                btn = page.get_by_role("button", name=btn_text, exact=True)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(force=True, timeout=5000)
                    logger.info("[%s] 投稿按钮已点击 (get_by_role '%s')", self.channel_name, btn_text)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                continue

        logger.warning("[%s] 所有投稿按钮点击方式均失败", self.channel_name)
        save_screenshot(page, "bilibili_video_publish_fail")
        raise RuntimeError("B站未找到投稿按钮")

        logger.warning("[%s] 所有发布按钮点击方式均失败", self.channel_name)
        save_screenshot(page, "bilibili_video_publish_fail")
        raise RuntimeError("B站未找到发布按钮")
