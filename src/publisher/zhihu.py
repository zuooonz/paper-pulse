"""知乎创作者 publisher（专栏文章）— Playwright 浏览器自动化方案

技术路线（参考 MediaCrawler）：
- Playwright + channel="chrome" 使用系统 Chrome（指纹更真实）
- 有头模式（headless=False）绕过知乎无头检测
- Cookie 注入登录态

注意：发布时会短暂弹出 Chrome 窗口，完成后自动关闭。
"""
from __future__ import annotations
import logging
import os
import traceback
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import launch_browser_context, parse_cookie_string, save_screenshot
from tools import storage

logger = logging.getLogger(__name__)


def _parse_cookies(cookie_str: str) -> list[dict]:
    """将 'k1=v1; k2=v2' 解析为 Playwright add_cookies 可用的字典列表"""
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
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".zhihu.com",
            "path": "/",
            "secure": True,
        })
    return cookies


def _check_has_z_c0(cookie_str: str) -> bool:
    return "z_c0=" in (cookie_str or "")


def _md_to_plaintext(content: str) -> str:
    """Markdown → 纯文本（知乎 Draft.js 编辑器不支持原生 Markdown）"""
    from publisher._content_preprocessor import ContentPreprocessor
    return ContentPreprocessor("rich_text").process(content)


class ZhihuPublisher(BasePublisher):
    channel_name = "zhihu"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    # ------------------------------------------------------------------
    # Playwright 浏览器操作
    # ------------------------------------------------------------------
    def _publish_with_playwright(
        self, title: str, content: str, cover_images: Optional[List[str]] = None,
        image_doi_map: dict | None = None,
        check_cancel=None
    ) -> PublishResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(self.cookie, ".zhihu.com"))
            page = context.new_page()
            try:

                # 1) 进入写文页面验证登录态
                page.goto("https://zhuanlan.zhihu.com/write", wait_until="domcontentloaded", timeout=120000)
                # 等待编辑器渲染（SPA 需要 JS 执行后才能出现 Draft.js 编辑器）
                try:
                    page.wait_for_selector(".public-DraftEditor-content", timeout=30000)
                except Exception:
                    logger.warning("知乎编辑器未在 30s 内渲染，继续尝试...")
                page.wait_for_timeout(3000)

                url = page.url
                if "signin" in url or "login" in url:
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="Cookie 已失效，请重新绑定知乎账号",
                    )
                if "unhuman" in url:
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="知乎触发了安全验证，请在前端 Settings 重新绑定账号",
                    )

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 2) 上传封面图（如有）
                if cover_images:
                    self._upload_cover(page, cover_images[0])

                # 3) 输入标题
                self._fill_title(page, title)

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 4) 输入内容
                plain = _md_to_plaintext(content)
                self._fill_content(page, plain)

                # 4.4) 预热：确保 Draft.js 编辑器完全初始化，光标定位到末尾
                page.wait_for_timeout(1500)
                self._move_cursor_to_end_zhihu(page)
                page.wait_for_timeout(1000)

                # 4.5) 上传正文配图并替换占位符
                if cover_images and len(cover_images) > 1:
                    logger.info("Uploading 知乎 body images...")
                    url_map = self._upload_body_images(page, cover_images, image_doi_map)
                    if url_map:
                        self._replace_image_placeholders(page, url_map)
                        save_screenshot(page, "zhihu_body_images_replaced")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 4.6) 发布前验证：确认编辑器中有内容
                block_count = page.evaluate(
                    "() => document.querySelectorAll('.public-DraftEditor-content [data-block=\"true\"]').length"
                )
                logger.info("知乎编辑器 data-block 数量: %d", block_count)
                if block_count == 0:
                    save_screenshot(page, "zhihu_empty_editor_warning")
                    logger.warning("编辑器内容为空，可能 Draft.js state 未同步")

                # 5) 点击发布
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                url = self._click_publish(page)

                # 5.1) 处理发布设置面板（知乎新版可能弹出二次确认面板）
                page.wait_for_timeout(2500)
                panel_detected = self._handle_publish_panel(page)
                if panel_detected:
                    logger.info("检测到发布设置面板，已处理")
                    page.wait_for_timeout(2000)

                # 5.2) 等待知乎后台保存完成
                logger.info("知乎发布请求已提交，等待后台保存完成...")
                publish_ok = False
                fail_reason = None
                for i in range(60):  # 最多等 60s
                    page.wait_for_timeout(1000)
                    try:
                        current_url = page.url
                    
                        # 成功信号 1: 页面跳转至文章页（无 /edit）
                        if "/p/" in current_url and "/edit" not in current_url:
                            logger.info("页面已跳转至文章页，保存完成")
                            url = current_url
                            publish_ok = True
                            break
                    
                        # 成功信号 2: DOM 中出现"发布成功"
                        page_text = page.evaluate("""() => document.body.innerText""") or ""
                        if "发布成功" in page_text:
                            logger.info("检测到'发布成功'提示")
                            publish_ok = True
                            break
                    
                        # 失败信号 1: 跳转到了登录/验证页
                        if "login" in current_url or "signin" in current_url or "unhuman" in current_url:
                            fail_reason = "发布过程中被重定向到登录/验证页"
                            logger.warning(fail_reason)
                            break
                    
                        # 失败信号 2: 页面出现错误提示
                        if "发布失败" in page_text or "网络异常" in page_text or "请重试" in page_text:
                            fail_reason = "页面提示发布失败"
                            logger.warning(fail_reason)
                            break
                    
                        # 调试：每 5 秒记录一次状态
                        if i % 5 == 0 and i > 0:
                            logger.info("等待发布完成... 当前URL=%s", current_url)
                    except Exception as e:
                        # 页面导航导致执行上下文被销毁，等待稳定后重试
                        logger.info("页面导航中，等待稳定... (%s)", str(e)[:80])
                        page.wait_for_timeout(2000)
                        current_url = page.url
                        if "/p/" in current_url and "/edit" not in current_url:
                            logger.info("页面已跳转至文章页（导航后）")
                            url = current_url
                            publish_ok = True
                            break
                else:
                    logger.warning("等待 60s 结束，未检测到发布成功")

                save_screenshot(page, "zhihu_after_publish")
                browser.close()

                if publish_ok and url and "/p/" in url:
                    return PublishResult(
                        success=True, channel=self.channel_name, url=url
                    )
                if fail_reason:
                    return PublishResult(
                        success=False, channel=self.channel_name, error=fail_reason,
                        url=url if url and "zhihu.com" in url else None,
                        raw={"current_url": url},
                    )
                return PublishResult(
                    success=False,
                    channel=self.channel_name,
                    error="发布可能未完成，未检测到发布成功或有效文章 URL",
                    url=url if url and "zhihu.com" in url else None,
                    raw={"current_url": url, "publish_ok": publish_ok},
                )

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
    # ------------------------------------------------------------------
    # 页面操作封装
    # ------------------------------------------------------------------
    def _upload_cover(self, page, img_path: str):
        if not img_path:
            return
        p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("封面图不存在: %s", img_path)
            return
        try:
            # 等待封面图上传区域渲染（SPA 延迟）
            page.wait_for_selector(".UploadPicture-wrapper, [class*='UploadPicture']", timeout=10000)
            # 尝试多种封面图上传选择器
            selectors = [
                "input[type='file'][accept='.jpeg, .jpg, .png']",
                "input[type='file'][accept*='image']",
                "input[type='file'][accept*='jpg']",
                "input[type='file'][accept*='png']",
                ".UploadPicture-wrapper input[type='file']",
                "input[type='file']",
            ]
            upload_input = None
            for sel in selectors:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    upload_input = loc
                    logger.info("封面图选择器命中: %s", sel)
                    break
            if upload_input is None:
                logger.warning("封面图上传 input 未找到")
                return
            # Playwright set_input_files 不需要元素可见，直接设置即可
            upload_input.set_input_files(str(p.resolve()))
            logger.info("封面图已上传: %s", p)
            page.wait_for_timeout(5000)
        except Exception as e:
            logger.warning("封面上传失败: %s", e)

    def _fill_title(self, page, title: str):
        title = title[:100]
        # 尝试多种标题选择器
        selectors = [
            "textarea.Input[placeholder*='标题']",
            "textarea[placeholder*='标题']",
            "input[placeholder*='标题']",
            "[class*='title'] textarea",
            "[class*='title'] input",
        ]
        for sel in selectors:
            if page.locator(sel).count() > 0:
                page.locator(sel).first.fill(title)
                logger.info("标题已输入: %s", title)
                page.wait_for_timeout(500)
                return
        logger.warning("标题输入框未找到")

    def _fill_content(self, page, content: str):
        """使用 clipboard paste 填入正文，比 fill() 更可靠地触发 Draft.js state 更新。"""
        selectors = [
            ".public-DraftEditor-content",
            "[contenteditable='true']",
            "[class*='editor'] [contenteditable]",
        ]
        for sel in selectors:
            if page.locator(sel).count() > 0:
                editor = page.locator(sel).first
                editor.click()
                page.wait_for_timeout(300)
                # 清空现有内容
                page.keyboard.press("Control+A")
                page.wait_for_timeout(200)
                page.keyboard.press("Backspace")
                page.wait_for_timeout(200)
                # Clipboard paste 填入（Draft.js 能正确识别）
                normalized = content.replace("\r\n", "\n")
                page.evaluate("""(text) => {
                    const editor = document.querySelector('.public-DraftEditor-content');
                    if (!editor) return;
                    const dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    const pasteEvent = new ClipboardEvent('paste', {
                        clipboardData: dt,
                        bubbles: true,
                        cancelable: true
                    });
                    editor.dispatchEvent(pasteEvent);
                }""", normalized)
                logger.info("内容已粘贴（长度 %d）", len(normalized))
                page.wait_for_timeout(2000)
                # 触发 blur 让 Draft.js 保存 state
                page.evaluate("""() => {
                    const editor = document.querySelector('.public-DraftEditor-content');
                    if (editor) editor.blur();
                }""")
                page.wait_for_timeout(500)
                return
        logger.warning("内容编辑器未找到")

    def _click_publish(self, page) -> Optional[str]:
        """点击发布按钮。不等待发布完成，只负责触发点击。"""
        # 先关闭可能的弹窗/遮罩层
        for _ in range(3):
            try:
                page.evaluate("""() => {
                    document.querySelectorAll('.Modal-backdrop, .Modal-wrapper, [role="dialog"], [class*="modal"]').forEach(el => {
                        if (el.style.display !== 'none') {
                            const closeBtn = el.querySelector('button[class*="close"], [aria-label="关闭"]');
                            if (closeBtn) closeBtn.click();
                        }
                    });
                }""")
                page.wait_for_timeout(500)
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                pass

        # 尝试多种发布按钮选择器
        selectors = [
            'button:has-text("发布文章")',
            'button.Button--primary:has-text("发布")',
            "button.Button--primary.Button--blue",
            'button[type="button"]:has-text("发布")',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(force=True)
                    logger.info("发布按钮已点击 (%s)", sel)
                    return page.url
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                continue

        # 兜底：JS 精确匹配
        try:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const publishBtn = btns.find(b => {
                    const t = b.textContent.trim();
                    return (t === '发布' || t === '发布文章') && !b.disabled;
                });
                if (publishBtn) publishBtn.click();
            }""")
            logger.info("发布按钮已点击 (JS fallback)")
        except Exception as e:
            logger.warning("发布按钮点击失败: %s", e)

        return page.url

    # ------------------------------------------------------------------
    # BasePublisher 接口
    # ------------------------------------------------------------------
    def _move_cursor_to_end_zhihu(self, page):
        """将光标定位到 Draft.js 编辑器最后一个 block 末尾。"""
        page.evaluate("""() => {
            const editor = document.querySelector('.public-DraftEditor-content');
            if (!editor) return;
            const blocks = editor.querySelectorAll('[data-block="true"]');
            const target = blocks.length > 0 ? blocks[blocks.length - 1] : editor.lastElementChild;
            if (target) {
                const range = document.createRange();
                range.selectNodeContents(target);
                range.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                editor.focus();
            }
        }""")
        page.wait_for_timeout(500)

    def _upload_body_images(self, page, cover_images: list[str], image_doi_map: dict | None = None) -> dict[str, str]:
        """在知乎 Draft.js 编辑器中上传正文图片，返回 {原始路径: 上传后URL}。

        策略：用 JS 将光标定位到 editor 最后一个 block 末尾，
        再依次插入图片，每张后按 Enter 两次并输入引用说明，避免堆叠。
        支持失败自动重试一次。
        """
        if not cover_images or len(cover_images) <= 1:
            return {}
        results: dict[str, str] = {}
        for idx, path in enumerate(cover_images[1:], start=1):
            resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
            if not resolved or not resolved.exists():
                logger.warning("知乎 body image not found: %s", path)
                continue

            def try_upload():
                """单次上传尝试，返回 (url, all_figure_urls_before, all_figure_urls_after)"""
                before_urls = set(page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('figure img')).map(i => i.getAttribute('data-original-src') || i.src);
                }""") or [])
                self._move_cursor_to_end_zhihu(page)
                url = self._upload_single_body_image_zhihu(page, str(resolved), skip_editor_click=True)
                after_urls = set(page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('figure img')).map(i => i.getAttribute('data-original-src') || i.src);
                }""") or [])
                new_urls = [u for u in after_urls if u not in before_urls and u and u.startswith('http')]
                # 如果提取到 URL，额外等待 1s 让 figure 渲染，再确认
                if url and new_urls:
                    page.wait_for_timeout(1000)
                return url, before_urls, after_urls, new_urls

            url, before_urls, after_urls, new_urls = try_upload()
            is_success = bool(url and new_urls)

            if is_success:
                results[path] = url
                logger.info("知乎 body image uploaded: %s -> %s", path, url)
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)
                page.keyboard.press("Enter")
                page.wait_for_timeout(200)
                doi = (image_doi_map or {}).get(path)
                if doi:
                    page.keyboard.type(f"（引自：{doi}）")
                    page.wait_for_timeout(300)
            else:
                logger.warning("知乎 body image upload failed (new_urls=%d): %s", len(new_urls), path)
                logger.info("知乎 body image retrying: %s", path)
                page.wait_for_timeout(3000)
                url2, before_urls2, after_urls2, new_urls2 = try_upload()
                if url2 and new_urls2:
                    results[path] = url2
                    logger.info("知乎 body image uploaded on retry: %s -> %s", path, url2)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(200)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(200)
                    doi = (image_doi_map or {}).get(path)
                    if doi:
                        page.keyboard.type(f"（引自：{doi}）")
                        page.wait_for_timeout(300)
                else:
                    logger.warning("知乎 body image retry failed (new_urls=%d): %s", len(new_urls2), path)

            # 上传间隔，避免触发平台限制
            if idx < len(cover_images) - 1:
                page.wait_for_timeout(3000)
        return results

    def _handle_publish_panel(self, page) -> bool:
        """处理知乎发布设置面板（二次确认）。返回是否检测到面板。"""
        try:
            # 轮询检测面板是否出现（最多 5s）
            panel_indicators = ["文章话题", "添加封面", "投稿至问题", "创作声明", "发布设置"]
            has_panel = False
            for _ in range(5):
                page_text = page.evaluate("""() => document.body.innerText""") or ""
                if any(indicator in page_text for indicator in panel_indicators):
                    has_panel = True
                    break
                page.wait_for_timeout(1000)
            if not has_panel:
                return False

            logger.info("检测到知乎发布设置面板")
            save_screenshot(page, "zhihu_publish_panel_detected")
            page.wait_for_timeout(1500)  # 等面板动画完成

            # 策略1: 精确查找包含"发布文章"文本的按钮（最可靠）
            all_btns = page.locator('button').all()
            for b in all_btns:
                try:
                    if b.is_visible() and "发布文章" in (b.inner_text() or ""):
                        b.click(force=True)
                        logger.info("已点击面板中的'发布文章'按钮")
                        page.wait_for_timeout(2000)
                        return True
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_handle_publish_panel）', exc_info=True)
                    continue

            # 策略2: 通过 CSS 选择器命中 .PublishPanel 底部按钮
            panel_btn = page.locator('.PublishPanel-stepTwoButtonWrapper button, .PublishPanel button:has-text(\"发布文章\")').first
            if panel_btn.count() > 0 and panel_btn.is_visible():
                panel_btn.click(force=True)
                logger.info("已点击发布设置面板确认按钮 (CSS selector)")
                page.wait_for_timeout(2000)
                return True

            # 策略3: 查找面板中可见的蓝色主按钮
            primary_btns = page.locator('button.Button--primary').all()
            visible_btns = [b for b in primary_btns if b.is_visible()]
            if visible_btns:
                visible_btns[-1].click(force=True)
                logger.info("已点击面板中最后一个可见主按钮")
                page.wait_for_timeout(2000)
                return True

            # 策略4: JS 兜底
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const visible = btns.filter(b => b.offsetParent !== null);
                for (let i = visible.length - 1; i >= 0; i--) {
                    const b = visible[i];
                    const text = b.innerText.trim();
                    if (text === '发布文章') { b.click(); return; }
                }
                for (let i = visible.length - 1; i >= 0; i--) {
                    const b = visible[i];
                    const text = b.innerText.trim();
                    const isBlue = b.className.includes('primary') || b.className.includes('blue') ||
                                   getComputedStyle(b).backgroundColor.includes('rgb(0, 132, 255)');
                    if ((text.includes('发布') || text.includes('确认')) && isBlue) {
                        b.click();
                        return;
                    }
                }
            }""")
            logger.info("已通过 JS 兜底点击面板确认按钮")
            page.wait_for_timeout(2000)
            return True
        except Exception as e:
            logger.warning("处理发布设置面板异常: %s", e)
            return False

    def _safe_close_modal_zhihu(self, page):
        """安全关闭知乎 modal：先点关闭按钮，再按 Escape，避免 removeChild 破坏 React 状态。"""
        try:
            close_btns = page.locator('button[aria-label="关闭"], .Modal-closeButton').all()
            for btn in close_btns:
                if btn.is_visible():
                    btn.click(force=True)
                    page.wait_for_timeout(400)
                    return
        except Exception:
            logger.debug('可选步骤失败，继续执行（_safe_close_modal_zhihu）', exc_info=True)
            pass
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_safe_close_modal_zhihu）', exc_info=True)
            pass
        try:
            backdrop = page.locator('.Modal-backdrop').first
            if backdrop.count() > 0 and backdrop.is_visible():
                backdrop.click(force=True)
                page.wait_for_timeout(400)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_safe_close_modal_zhihu）', exc_info=True)
            pass

    def _upload_single_body_image_zhihu(self, page, image_path: str, skip_editor_click: bool = False) -> str | None:
        """知乎定制单图上传：走官方 UI 流程（modal → filechooser → insert）。"""
        try:
            # 0) 安全关闭任何残留 modal
            self._safe_close_modal_zhihu(page)

            # 1) 确保编辑器有焦点（外部已定位光标时可跳过）
            if not skip_editor_click:
                editor = page.locator(".public-DraftEditor-content, [contenteditable='true']").first
                editor.click(force=True)
                page.wait_for_timeout(300)

            # 2) 点击编辑器图片按钮打开模态框
            img_btn = page.locator('button[aria-label*="图片"]').first
            if img_btn.count() == 0:
                # fallback: 遍历可见 toolbar 按钮匹配 aria-label/title 含"图片"
                btns = page.locator('button, [role="button"]').all()
                for b in btns:
                    label = b.get_attribute('aria-label') or b.get_attribute('title') or ''
                    if '图片' in label and b.is_visible():
                        img_btn = b
                        break
            if not img_btn or (hasattr(img_btn, 'count') and img_btn.count() == 0):
                logger.warning("知乎图片按钮未找到")
                return None
            img_btn.click(force=True)
            page.wait_for_timeout(2500)

            # 3) 找到 Modal 内的 file input（拖拽上传区）并使其可见
            modal_input = page.locator('.Modal-content input[type="file"]').first
            if modal_input.count() == 0:
                modal_input = page.locator('.Modal-wrapper input[type="file"]').first
            if modal_input.count() == 0:
                inputs = page.locator('input[type="file"]').all()
                for inp in inputs:
                    if inp.is_visible():
                        modal_input = inp
                        break
            if not modal_input or (hasattr(modal_input, 'count') and modal_input.count() == 0):
                logger.warning("知乎 Modal file input 未找到")
                self._safe_close_modal_zhihu(page)
                return None
            modal_input.evaluate(
                "el => { el.style.display='block'; el.style.visibility='visible'; el.style.opacity='1';"
                "el.style.position='fixed'; el.style.zIndex='99999'; el.style.top='50%'; el.style.left='50%';"
                "el.style.width='200px'; el.style.height='50px'; }"
            )
            page.wait_for_timeout(300)

            # 4) 触发 filechooser 并选择文件
            with page.expect_file_chooser(timeout=5000) as fc_info:
                modal_input.click(force=True)
            fc = fc_info.value
            fc.set_files(image_path)
            logger.info("知乎图片已选择: %s", image_path)
            page.wait_for_timeout(3000)

            # 5) 点击"插入图片"按钮
            insert_btn = page.locator('button:has-text("插入图片")').first
            if insert_btn.count() > 0 and insert_btn.is_visible():
                insert_btn.click(force=True)
            else:
                page.evaluate(
                    "() => { const btns = Array.from(document.querySelectorAll('button'));"
                    "const b = btns.find(x => x.textContent.includes('插入图片')); if (b) b.click(); }"
                )
            page.wait_for_timeout(5000)

            # 6) 等待 modal 完全关闭
            for _ in range(10):
                modal = page.locator('.Modal-wrapper, .Modal-content').first
                if modal.count() == 0 or not modal.is_visible():
                    break
                page.wait_for_timeout(500)

            # 7) 提取插入后的图片 URL
            # 知乎 Draft.js 编辑器中图片以 <figure> 包裹，data-original-src 为真实 URL
            url = page.evaluate("""() => {
                const imgs = document.querySelectorAll('figure img');
                if (imgs.length === 0) return null;
                const last = imgs[imgs.length - 1];
                return last ? (last.getAttribute('data-original-src') || last.src) : null;
            }""")
            if url and isinstance(url, str) and url.startswith("http"):
                return url
            return None
        except Exception as e:
            logger.warning("知乎单图上传异常: %s", e)
            return None

    def _replace_image_placeholders(self, page, url_map: dict[str, str]):
        """知乎不再使用占位符策略，此方法保留为兼容接口，无实际操作。"""
        # 图片和引用说明已在 _upload_body_images 中直接插入编辑器，无需清理占位符
        pass

    def process_body_images(
        self,
        content: str,
        image_paths: list[str],
        image_doi_map: dict[str, str] | None = None,
    ) -> str:
        """知乎：不预置 [图片: 论文配图] 占位符。
        图片和引用说明由 _upload_body_images 在编辑器末尾动态插入。"""
        return content

    def publish(
        self,
        draft_id: int,
        title: str,
        content: str,
        cover_images: Optional[List[str]] = None,
        **kwargs,
    ) -> PublishResult:
        cookie = self.cookie
        if not cookie:
            return PublishResult(
                success=False,
                channel=self.channel_name,
                error="Zhihu cookie not configured",
            )

        if not _check_has_z_c0(cookie):
            return PublishResult(
                success=False,
                channel=self.channel_name,
                error="Cookie 缺少 z_c0（知乎核心认证 token）。"
                       "请从浏览器 F12 → Network → 请求 Headers → Cookie 整行复制，"
                       "不要只复制 document.cookie。",
            )

        image_doi_map = kwargs.get("image_doi_map") or {}
        # 知乎不预置占位符，图片和引用在上传阶段动态插入
        content = self.process_body_images(content, cover_images or [], image_doi_map=image_doi_map)

        try:
            check_cancel = kwargs.get("check_cancel")
            return self._publish_with_playwright(title, content, cover_images, image_doi_map=image_doi_map, check_cancel=check_cancel)
        except Exception as e:
            logger.error("知乎发布失败: %s", e)
            logger.error(traceback.format_exc())
            return PublishResult(
                success=False, channel=self.channel_name, error="发布失败，请稍后重试"
            )
