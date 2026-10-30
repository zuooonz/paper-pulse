"""CSDN 博客 publisher — Markdown 编辑器方案

参考 Article-Auto-Publisher 思路：
- 使用 CSDN Markdown 编辑器 (editor.csdn.net/md/)
- 通过剪贴板粘贴事件填入 Markdown 正文（保留原始格式）
- 发布流程：btn-publish 调出面板 → 配置标签/封面/摘要 → btn-b-red 最终发布
"""
from __future__ import annotations
import logging
import os
import traceback
from pathlib import Path
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import parse_cookie_string, launch_browser_context, save_screenshot
from tools import storage

logger = logging.getLogger(__name__)

_EDITOR_URL = "https://editor.csdn.net/md/"


class CsdnPublisher(BasePublisher):
    channel_name = "csdn"

    def __init__(self, cookie: str, author_username: str = "",
                 default_tags: str | None = None,
                 timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.author_username = author_username
        if default_tags is None:
            from core.domain_config import domain_tags
            default_tags = ",".join(domain_tags())
        self.default_tags = default_tags
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    def publish(self, draft_id: int, title: str, content: str,
                tags: str = "", description: str = "", cover_images=None, **kwargs) -> PublishResult:
        cookie = self.cookie
        if not cookie:
            return PublishResult(success=False, channel=self.channel_name, error="CSDN cookie not configured")

        # Use configured default tags when this publication has no explicit tags.
        tags = tags or self.default_tags
        image_doi_map = kwargs.get("image_doi_map") or {}

        # 追加剩余图片到正文（markdown 占位符 + 图注）
        content = self.process_body_images(content, cover_images or [], image_doi_map=image_doi_map)

        try:
            check_cancel = kwargs.get("check_cancel")
            return self._publish_with_playwright(title, content, tags, description, cover_images, image_doi_map=image_doi_map, check_cancel=check_cancel)
        except Exception as e:
            logger.error("CSDN publish failed: %s", e)
            logger.error(traceback.format_exc())
            return PublishResult(success=False, channel=self.channel_name, error="发布失败，请稍后重试")

    def process_body_images(self, content, image_paths, image_doi_map=None):
        # CSDN: 不预插入占位符；上传后图片自动插入编辑器光标位置
        return content

    def _upload_body_images(self, page, cover_images: list[str], image_doi_map: dict | None = None) -> dict[str, str]:
        """在 CSDN Markdown 编辑器中上传正文图片，返回 {原始路径: 上传后URL}"""
        if not cover_images or len(cover_images) <= 1:
            return {}
        results: dict[str, str] = {}
        for path in cover_images[1:]:
            resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
            if not resolved or not resolved.exists():
                logger.warning("CSDN body image not found: %s", path)
                continue
            try:
                # CSDN 图片上传流程：
                # 1) 点击图片按钮 → modal 弹窗
                # 2) 点击 .uploadPicture 触发文件选择器（不能直接操作 input）
                # 3) 上传完成后 URL 自动插入 Monaco Editor DOM（非 <img> 标签，而是 span 文本）
                # 4) 从 DOM HTML 正则提取 i-blog.csdnimg.cn URL

                # 记录上传前的所有图片 URL
                before_urls = set(page.evaluate(r"""() => {
                    const html = document.body.innerHTML;
                    const matches = html.match(/https:\/\/i-blog\.csdnimg\.cn\/direct\/[a-f0-9]+\.png/g);
                    return matches || [];
                }""") or [])

                # 确保光标在编辑器末尾，图片会插入到内容最后
                page.keyboard.press("Control+End")
                page.wait_for_timeout(300)

                btn = page.locator('button[data-title*="图片"]').first
                if btn.count() == 0:
                    logger.warning("CSDN image button not found")
                    continue
                btn.click()
                page.wait_for_timeout(2500)

                upload_div = page.locator('.uploadPicture').first
                if upload_div.count() == 0:
                    logger.warning("CSDN uploadPicture div not found")
                    continue

                with page.expect_file_chooser(timeout=5000) as fc_info:
                    upload_div.click()
                fc = fc_info.value
                fc.set_files(str(resolved.resolve()))
                logger.info("CSDN file chooser set for %s", path)
                page.wait_for_timeout(10000)  # CSDN 上传链：signature → OBS → 插入编辑器

                # 关闭 modal（如果仍打开）
                try:
                    close_btn = page.locator('.modal__close-button').first
                    if close_btn.count() > 0 and close_btn.is_visible():
                        close_btn.click()
                        page.wait_for_timeout(500)
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_body_images）', exc_info=True)
                    pass

                # 从 DOM HTML 提取新出现的 i-blog.csdnimg.cn URL
                after_urls = set(page.evaluate(r"""() => {
                    const html = document.body.innerHTML;
                    const matches = html.match(/https:\/\/i-blog\.csdnimg\.cn\/direct\/[a-f0-9]+\.png/g);
                    return matches || [];
                }""") or [])

                new_urls = [u for u in after_urls if u not in before_urls]
                url = new_urls[0] if new_urls else None
                if url:
                    # 去掉 #pic_center 等 hash，记录干净 URL
                    clean_url = url.split('#')[0]
                    results[path] = clean_url
                    logger.info("CSDN body image uploaded: %s -> %s", path, clean_url)
                    # 追加引用说明
                    doi = (image_doi_map or {}).get(path)
                    if doi:
                        page.wait_for_timeout(500)
                        page.keyboard.type(f"\n> （引自：{doi}）", delay=10)
                        page.wait_for_timeout(300)
                else:
                    logger.warning("CSDN body image upload failed (no URL extracted): %s", path)
            except Exception as e:
                logger.warning("CSDN body image upload exception for %s: %s", path, e)
        return results

    def _replace_image_placeholders(self, page, url_map: dict[str, str]):
        """用 JS 在 Monaco Editor 中替换图片占位符的本地路径为上传后的 URL"""
        if not url_map:
            return
        for local_path, url in url_map.items():
            try:
                # 同时尝试原始路径和正斜杠版本（Windows 反斜杠问题）
                paths_to_try = [local_path, local_path.replace("\\", "/")]
                for p in paths_to_try:
                    result = page.evaluate(r"""(localPath, remoteUrl) => {
                        const escaped = localPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                        const pattern = '!\\[论文配图\\]\\(' + escaped + '\\)';
                        // 策略1: Monaco Editor API
                        try {
                            const editors = monaco.editor.getEditors();
                            if (editors && editors.length > 0) {
                                const model = editors[0].getModel();
                                const text = model.getValue();
                                const regex = new RegExp(pattern, 'g');
                                const newText = text.replace(regex, '![论文配图](' + remoteUrl + ')');
                                if (newText !== text) {
                                    model.setValue(newText);
                                    return {ok: true, method: 'monaco'};
                                }
                            }
                        } catch(e) {}
                        // 策略2: 直接操作 textarea
                        const ta = document.querySelector('.monaco-editor textarea');
                        if (ta) {
                            const text = ta.value;
                            const regex = new RegExp(pattern, 'g');
                            const newText = text.replace(regex, '![论文配图](' + remoteUrl + ')');
                            if (newText !== text) {
                                ta.value = newText;
                                ta.dispatchEvent(new Event('input', { bubbles: true }));
                                return {ok: true, method: 'textarea'};
                            }
                        }
                        return {ok: false};
                    }""", p, url)
                    if result and result.get("ok"):
                        logger.info("CSDN placeholder replaced for %s (method=%s)", p, result.get("method"))
                        break
            except Exception as e:
                logger.debug("CSDN replace placeholder failed for %s: %s", local_path, e)

    def _publish_with_playwright(self, title: str, content: str,
                                 tags: str = "", description: str = "",
                                 cover_images: Optional[List[str]] = None,
                                 image_doi_map: dict | None = None,
                                 check_cancel=None) -> PublishResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(self.cookie, ".csdn.net"))
            page = context.new_page()
            try:

                # 1) 进入 Markdown 编辑器
                logger.info("Navigating to CSDN Markdown editor: %s", _EDITOR_URL)
                page.goto(_EDITOR_URL, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(2000)
                save_screenshot(page, "csdn_editor_loaded")

                url = page.url
                if "login" in url or "passport" in url:
                    save_screenshot(page, "csdn_login_redirect")
                    browser.close()
                    return PublishResult(success=False, channel=self.channel_name, error="CSDN cookie expired")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 2) 关闭可能的弹窗/新手引导/遮罩层
                self._close_modals(page)

                # 3) 填入标题（参考 csdn-auto-publish：nativeInputValueSetter + dispatchEvent）
                logger.info("Filling CSDN title")
                title_loc = page.locator(".article-bar__title, input[placeholder*='标题']").first
                title_loc.wait_for(state="attached", timeout=10000)
                title_loc.evaluate("""(el, text) => {
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(el, text);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new InputEvent('input', {
                        bubbles: true, data: text, inputType: 'insertText'
                    }));
                }""", title[:100])
                page.wait_for_timeout(500)
                save_screenshot(page, "csdn_title_filled")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 4) 填入正文（剪贴板粘贴，保留 Markdown 格式）
                logger.info("Filling CSDN content via clipboard paste")
                editor_loc = page.locator(".editor__inner, .monaco-editor").first
                editor_loc.click()
                page.wait_for_timeout(500)
                page.keyboard.press("Control+A")
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(500)

                normalized = content.replace("\r\n", "\n")
                page.evaluate("""(text) => {
                    let target = document.activeElement;
                    if (!target || target.tagName !== 'TEXTAREA') {
                        target = document.querySelector('.monaco-editor textarea') || document.activeElement;
                    }
                    const dataTransfer = new DataTransfer();
                    dataTransfer.setData('text/plain', text);
                    const event = new ClipboardEvent('paste', {
                        clipboardData: dataTransfer,
                        bubbles: true,
                        cancelable: true
                    });
                    target.dispatchEvent(event);
                }""", normalized)
                page.wait_for_timeout(2000)
                save_screenshot(page, "csdn_content_filled")

                # 4.5) 上传正文配图并替换占位符
                if cover_images and len(cover_images) > 1:
                    logger.info("Uploading CSDN body images...")
                    url_map = self._upload_body_images(page, cover_images, image_doi_map)
                    if url_map:
                        self._replace_image_placeholders(page, url_map)
                        save_screenshot(page, "csdn_body_images_replaced")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 5) 点击"发布文章"调出发布面板
                logger.info("Opening CSDN publish panel")
                self._close_modals(page)
                publish_panel_btn = page.locator("button.btn-publish").first
                publish_panel_btn.wait_for(state="visible", timeout=10000)
                publish_panel_btn.click()
                page.wait_for_selector("text=添加文章标签", timeout=10000)
                page.wait_for_timeout(500)
                save_screenshot(page, "csdn_publish_panel_open")

                # 6) 填写标签
                tag_list = [t.strip() for t in (tags or self.default_tags).split(",") if t.strip()]
                if tag_list:
                    logger.info("Filling CSDN tags: %s", tag_list)
                    self._fill_tags(page, tag_list)
                    save_screenshot(page, "csdn_tags_filled")

                # 7) 上传封面图
                if cover_images:
                    logger.info("Uploading CSDN cover")
                    cover_path = self._resolve_cover_path(cover_images[0])
                    if cover_path:
                        self._upload_cover(page, cover_path)
                        save_screenshot(page, "csdn_cover_uploaded")

                # 8) 填写摘要（注意：不能调 _close_modals，会把发布弹窗也删掉）
                if description:
                    logger.info("Filling CSDN summary")
                    self._fill_summary(page, description)
                    save_screenshot(page, "csdn_summary_filled")

                # 9) 选择文章类型（默认原创）
                self._select_article_type(page)

                # 10) 等待表单校验刷新，最终发布
                logger.info("Clicking final publish button")
                page.wait_for_timeout(2000)

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # JS 精确定位最大可见 dialog 内的"发布文章"按钮
                # 避免 get_by_text 匹配到编辑器底部被遮挡的背景按钮
                # Playwright locator: 在包含"文章摘要"的 dialog 内找发布按钮
                try:
                    final_btn = page.locator(".el-dialog:has-text('文章摘要') button.btn-b-red, [role='dialog']:has-text('文章摘要') button.btn-b-red").first
                    if final_btn.count() > 0:
                        final_btn.click(force=True)
                        logger.info("Final publish button clicked (has-text dialog)")
                    else:
                        raise Exception("no button in dialog")
                except Exception as e:
                    logger.warning("Dialog-scoped click failed: %s, trying global last btn-b-red", e)
                    try:
                        # fallback: 找所有可见的 btn-b-red，点击最后一个（通常是发布面板内的）
                        page.evaluate("""() => {
                            const reds = Array.from(document.querySelectorAll('button.btn-b-red'));
                            const visible = reds.filter(b => {
                                const r = b.getBoundingClientRect();
                                return r.width > 0 && r.height > 0 && window.getComputedStyle(b).display !== 'none';
                            });
                            if (visible.length > 0) {
                                visible[visible.length - 1].click();
                                return {clicked: true, count: visible.length};
                            }
                            return {clicked: false};
                        }""")
                        logger.info("Final publish button clicked (last visible btn-b-red)")
                    except Exception as e2:
                        logger.error("All publish click attempts failed: %s", e2)
                        browser.close()
                        return PublishResult(success=False, channel=self.channel_name, error=f"发布按钮点击失败: {e2}")

                page.wait_for_timeout(8000)
                current_url = page.url
                save_screenshot(page, "csdn_after_publish")

                is_success = "editor" not in current_url and "blog" in current_url
                if not is_success:
                    page_msg = page.evaluate("""() => {
                        const el = document.querySelector('.el-message, .el-notification, [class*="toast"]');
                        if (el) return el.textContent.trim();
                        const dialog = document.querySelector('.el-message-box__message, .el-dialog__body');
                        if (dialog && dialog.textContent.includes('上限')) return dialog.textContent.trim();
                        return '';
                    }""")
                    if "上限" in page_msg:
                        error_msg = f"CSDN 发文失败：今日已达发文上限。{page_msg}"
                    else:
                        error_msg = f"发布未成功：页面仍停留在编辑器。URL: {current_url} msg: {page_msg}"
                    logger.warning("CSDN publish did not navigate away from editor. URL: %s msg: %s", current_url, page_msg)
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error=error_msg,
                        raw={"current_url": current_url, "page_msg": page_msg},
                    )

                browser.close()
                return PublishResult(
                    success=True,
                    channel=self.channel_name,
                    url=current_url if "csdn.net" in current_url else None,
                    raw={"current_url": current_url},
                )

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
    def _close_modals(self, page):
        """关闭可能拦截点击的弹窗、新手引导、遮罩层、下拉菜单"""
        # 1. 按 Escape 关闭下拉菜单/弹窗
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
            pass

        # 2. 关闭标准弹窗
        try:
            close_btn = page.locator("button.modal__close-button[aria-label='关闭']").first
            if close_btn.is_visible():
                logger.info("Closing CSDN modal popup")
                close_btn.click()
                page.wait_for_timeout(500)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
            pass

        # 3. 关闭标签选择弹窗/dropdown
        try:
            close_svg = page.locator(".el-dialog__header svg, .tag-dialog svg, .el-icon-close, .el-dialog__close").first
            if close_svg.is_visible():
                close_svg.click()
                page.wait_for_timeout(500)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
            pass

        # 4. JS 移除新手引导、遮罩层、高 z-index 元素
        try:
            page.evaluate("""() => {
                document.querySelectorAll('.beginnerGuide-box, .beginnerGuide, .guide-mask, .guide-overlay, .onboarding-mask, .mark_add_tag_right, .form-tag-box .el-select-dropdown, .el-autocomplete-suggestion, .el-dropdown-menu').forEach(el => el.remove());
                document.querySelectorAll('[class*="guide"], [class*="onboarding"], [class*="tutorial"], [class*="dropdown"], [class*="suggestion"]').forEach(el => {
                    const s = window.getComputedStyle(el);
                    if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) > 50) el.remove();
                });
            }""")
            page.wait_for_timeout(300)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
            pass

        # 5. 强制移除所有固定/绝对定位的高 z-index 大元素（保留编辑器、发布面板、按钮等关键元素）
        try:
            page.evaluate("""() => {
                document.querySelectorAll('*').forEach(el => {
                    const s = window.getComputedStyle(el);
                    if ((s.position === 'fixed' || s.position === 'absolute') && parseInt(s.zIndex) > 200 &&
                        !el.closest('.editor-container, .monaco-editor, .article-bar, .btn-publish, .btn-b-red, .el-textarea, .lab-switch, .el-dialog, .el-dialog__wrapper, .v-modal, .publish-dialog') &&
                        el.offsetWidth > 50 && el.offsetHeight > 50) {
                        el.remove();
                    }
                });
            }""")
            page.wait_for_timeout(300)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
            pass

    def _fill_tags(self, page, tags: List[str]):
        """在发布面板中填写标签（对齐参考项目：输入后按 Enter，不点下拉建议）"""
        # 先清空已有标签
        try:
            close_icons = page.locator("i.el-tag__close.el-icon-close")
            max_clears = 10
            while close_icons.count() > 0 and max_clears > 0:
                close_icons.first.click()
                page.wait_for_timeout(400)
                max_clears -= 1
        except Exception as e:
            logger.debug("No existing tags to clear: %s", e)

        add_tag_btn = page.locator("button.tag__btn-tag").first
        if add_tag_btn.count() == 0:
            logger.warning("Add tag button not found")
            return

        add_tag_btn.click(force=True)
        page.wait_for_timeout(2000)

        tag_input = page.locator("input.el-input__inner[placeholder*='请输入文字搜索']").first
        try:
            tag_input.wait_for(state="visible", timeout=10000)
        except Exception:
            tag_input = page.locator("input.el-input__inner").last
            tag_input.wait_for(state="visible", timeout=5000)

        for tag in tags[:5]:
            tag_input.click()
            page.wait_for_timeout(200)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(200)
            page.keyboard.type(tag, delay=50)
            page.wait_for_timeout(1000)
            # CSDN 必须从下拉列表选择已有标签；优先点击第一个建议项
            try:
                dropdown_sel = ".el-select-dropdown__item, .el-autocomplete-suggestion__list li, .tag-suggestion-item, .suggest-item, [class*='dropdown'] li"
                first_item = page.locator(dropdown_sel).first
                first_item.wait_for(state="visible", timeout=3000)
                first_item.click()
                page.wait_for_timeout(500)
                logger.info("标签 '%s' 已选择（下拉列表）", tag)
                continue
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_tags）', exc_info=True)
                pass
            # 无下拉列表则回车尝试
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            logger.info("标签 '%s' 已尝试 Enter 添加", tag)

        # 关闭标签输入弹窗（参考项目方式：SVG 关闭按钮，fallback 文章摘要）
        try:
            # X 图标 path（Element UI 关闭图标）
            exact_svg_path = "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z"
            dialog_close = page.locator(f"button svg:has(path[d='{exact_svg_path}']):visible, .el-dialog__header svg:has(path[d='{exact_svg_path}']):visible")
            if dialog_close.count() > 0:
                dialog_close.last.click()
            else:
                page.locator("text=文章摘要").first.click(force=True)
        except Exception:
            logger.debug('可选步骤失败，继续执行（_fill_tags）', exc_info=True)
            pass
        page.wait_for_timeout(1500)

    def _resolve_cover_path(self, img_path: str) -> Path | None:
        p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("Cover image not found: %s", img_path)
            return None
        return p.resolve()

    def _upload_cover(self, page, img_path: Path):
        """在发布面板中上传封面图"""
        try:
            # 先删除已有封面
            try:
                preview = page.locator("div.preview-box.hasImg").first
                if preview.count() > 0:
                    preview.hover()
                    page.wait_for_timeout(500)
                    page.locator("div.preview-box.hasImg a.btn-remove-coverimg").first.click(force=True)
                    page.wait_for_timeout(1000)
            except Exception:
                logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                pass

            # 点击上传按钮并等待文件选择器
            with page.expect_file_chooser(timeout=8000) as fc_info:
                page.locator("button.upload-img-box").first.click()
            file_chooser = fc_info.value
            file_chooser.set_files(str(img_path))

            # 等待确认上传弹窗
            try:
                page.wait_for_timeout(1500)
                confirm = page.locator("div.vicp-operate-btn:has-text('确认上传')").first
                confirm.wait_for(state="visible", timeout=5000)
                confirm.click(force=True)
                logger.info("Cover upload confirmed")
            except Exception as e:
                logger.debug("Cover confirm dialog skipped: %s", e)

            page.wait_for_timeout(4000)
        except Exception as e:
            logger.warning("CSDN cover upload failed: %s", e)

    def _fill_summary(self, page, summary: str):
        """在发布面板中填写摘要"""
        safe_summary = summary[:200] if summary else ""
        try:
            # 优先匹配摘要 textarea
            summary_loc = page.locator("textarea[placeholder*='展现列表'], textarea[placeholder*='摘要']").first
            if summary_loc.count() > 0:
                summary_loc.fill(safe_summary)
                logger.info("CSDN summary filled")
            else:
                fallback = page.locator("textarea").first
                if fallback.count() > 0:
                    fallback.fill(safe_summary)
                    logger.info("CSDN summary filled (fallback)")
        except Exception as e:
            logger.warning("CSDN summary fill failed: %s", e)

    def _select_article_type(self, page):
        """选择文章类型为原创（默认）"""
        try:
            original_label = page.locator("label[for='original']").first
            if original_label.count() > 0:
                original_label.click()
                page.wait_for_timeout(500)
                logger.info("CSDN article type set to original")
        except Exception as e:
            logger.debug("CSDN article type selection skipped: %s", e)
