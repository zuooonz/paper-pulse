"""哔哩哔哩专栏 publisher — Playwright 浏览器自动化方案

技术路线：
- Playwright sync API + Cookie 注入登录态
- 专栏编辑器在 iframe (york/read-editor) 中，使用 frame_locator 操作
- 正文使用 keyboard.type() 模拟打字（富文本编辑器）

登录态验证：访问 member.bilibili.com，若跳转至 passport 则 Cookie 失效。

流程：upload/text/edit → 点击"新的创作" → iframe 编辑器 → 填标题/正文 →
      发布设置(分类/原创) → 封面上传 → 点击"发布"
"""
from __future__ import annotations
import base64
import logging
import os
import random
import traceback
from pathlib import Path
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import launch_browser_context, parse_cookie_string, save_screenshot, save_page_html
from tools import storage

logger = logging.getLogger(__name__)


def _md_to_plaintext(content: str) -> str:
    """Markdown → 纯文本（哔哩哔哩专栏编辑器不支持原生 Markdown）"""
    from publisher._content_preprocessor import ContentPreprocessor
    return ContentPreprocessor("rich_text").process(content)


class BilibiliPublisher(BasePublisher):
    channel_name = "bilibili"

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
        check_cancel=None, cookie: str = ""
    ) -> PublishResult:
        from playwright.sync_api import sync_playwright

        effective_cookie = cookie or self.cookie
        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(effective_cookie, ".bilibili.com"))
            page = context.new_page()
            try:

                # 1) 进入专栏投稿首页验证登录态
                logger.info("[%s] 进入专栏投稿首页...", self.channel_name)
                page.goto("https://member.bilibili.com/platform/upload/text/edit", wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                save_screenshot(page, "bilibili_editor_loaded")

                if "passport" in page.url.lower() or "login" in page.url.lower():
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="Cookie 已失效，请重新绑定哔哩哔哩账号",
                    )

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 2) 点击"+ 新的创作"进入编辑器
                logger.info("[%s] 点击'新的创作'...", self.channel_name)
                try:
                    clicked = False
                    for text in ["新的创作", "+ 新的创作", "新创作"]:
                        try:
                            btn = page.get_by_text(text, exact=False)
                            if btn.count() > 0:
                                btn.first.click(timeout=5000)
                                clicked = True
                                break
                        except Exception:
                            logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                            continue
                    if not clicked:
                        page.evaluate("""() => {
                            const el = Array.from(document.querySelectorAll('*')).find(
                                e => e.textContent && e.textContent.includes('新的创作')
                            );
                            if (el) el.click();
                        }""")
                    page.wait_for_timeout(3000)
                except Exception as e:
                    logger.warning("[%s] 点击'新的创作'失败: %s", self.channel_name, e)

                save_screenshot(page, "bilibili_after_create")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 3) 定位编辑器 iframe（headless: read-editor, headed: read-draft）
                logger.info("[%s] 定位编辑器 iframe...", self.channel_name)
                frame = None
                for sel in [
                    "iframe[src*='york/read-editor']",
                    "iframe[src*='york/read-draft']",
                    "iframe[src*='read-editor']",
                    "iframe[src*='read-draft']",
                ]:
                    try:
                        fl = page.frame_locator(sel)
                        fl.locator("input[placeholder*='标题'], textarea[placeholder*='标题']").wait_for(
                            state="attached", timeout=5000
                        )
                        if fl.locator("input[placeholder*='标题'], textarea[placeholder*='标题']").count() > 0:
                            frame = fl
                            logger.info("[%s] iframe 定位成功: %s", self.channel_name, sel)
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                        continue

                if frame is None:
                    save_screenshot(page, "bilibili_iframe_error")
                    save_page_html(page, "bilibili_iframe_error")
                    try:
                        all_iframes = page.evaluate("""() => Array.from(document.querySelectorAll('iframe')).map(
                            f => ({src: f.src, id: f.id, className: f.className})
                        )""")
                        logger.info("[%s] 所有 iframe: %s", self.channel_name, all_iframes)
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                        pass
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error=f"编辑器 iframe 未找到，当前URL: {page.url}",
                    )

                # 4) 输入标题
                self._fill_title(frame, page, title)
                save_screenshot(page, "bilibili_title_filled")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 5) 输入正文
                plain = _md_to_plaintext(content)
                self._fill_content(frame, page, plain)
                save_screenshot(page, "bilibili_content_filled")

                # 5.5) 上传正文配图并替换占位符
                if cover_images and len(cover_images) > 1:
                    logger.info("[%s] Uploading body images...", self.channel_name)
                    self._upload_body_images(frame, page, cover_images, image_doi_map)
                    save_screenshot(page, "bilibili_body_images_done")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 6) 发布设置（分类、原创声明）+ 封面图
                self._setup_settings_and_cover(frame, page, cover_images[0] if cover_images else None)
                save_screenshot(page, "bilibili_settings_and_cover")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 8) 点击发布
                result_url = self._click_publish(frame, page)
                save_screenshot(page, "bilibili_after_publish")

                browser.close()

                if result_url == "__QUOTA_EXCEEDED__":
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="已达到当日投稿上限，文章已保存为草稿",
                    )
                if result_url:
                    return PublishResult(
                        success=True,
                        channel=self.channel_name,
                        url=result_url,
                    )
                return PublishResult(
                    success=True,
                    channel=self.channel_name,
                    raw={"current_url": page.url if page else None},
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
    def _fill_title(self, frame, page, title: str):
        """在 iframe 中填入标题"""
        title = title[:30]
        try:
            frame.locator("input[placeholder*='标题'], textarea[placeholder*='标题']").fill(title)
            logger.info("[%s] 标题已输入: %s", self.channel_name, title)
            page.wait_for_timeout(500)
        except Exception as e:
            logger.warning("[%s] 标题输入失败: %s", self.channel_name, e)

    def _fill_content(self, frame, page, content: str):
        """在 iframe 中填入正文（使用 keyboard.type 模拟打字）"""
        try:
            try:
                frame.get_by_role("paragraph").first.click()
            except Exception:
                frame.locator("[contenteditable='true']").first.click()
            page.wait_for_timeout(500)
            page.keyboard.type(content, delay=random.randint(8, 20))
            logger.info("[%s] 内容已输入（长度 %d）", self.channel_name, len(content))
            page.wait_for_timeout(1000)
        except Exception as e:
            logger.warning("[%s] 内容输入失败: %s", self.channel_name, e)

    def _setup_settings_and_cover(self, frame, page, cover_image_path: str | None):
        """设置分类、原创声明，并上传封面图（发布设置面板内完成）"""
        frame_obj = page.frame(url="*read-editor*")
        if frame_obj is None:
            frame_obj = page.frame(name="113")

        try:
            # 1) 点击"发布设置"展开面板
            frame.get_by_role("button", name="发布设置").click(timeout=5000)
            page.wait_for_timeout(800)

            # 2) 分类：新版编辑器发布设置面板中无分类选项，跳过
            #    （分类在发布时由系统根据内容自动推断）

            # 3) 原创声明 — 点击可见 checkbox 区域
            try:
                frame_obj.evaluate("""() => {
                    const items = Array.from(document.querySelectorAll('label, div, span, p'));
                    const decl = items.find(l => l.textContent.includes('声明此文章为原创'));
                    if (decl) {
                        const box = decl.querySelector('.vui_checkbox--input-box, .vui_checkbox, .checkbox')
                            || decl.closest('label, div')?.querySelector('.vui_checkbox--input-box, .vui_checkbox, .checkbox');
                        if (box) box.click();
                        else decl.click();
                    }
                }""")
                page.wait_for_timeout(300)
                logger.info("[%s] 原创声明已勾选", self.channel_name)
            except Exception as e:
                logger.warning("[%s] 原创声明设置失败: %s", self.channel_name, e)

            # 4) 上传封面图
            if cover_image_path:
                self._do_upload_cover(frame, page, frame_obj, cover_image_path)
        except Exception as e:
            logger.warning("[%s] 发布设置面板操作失败: %s", self.channel_name, e)

    def _do_upload_cover(self, frame, page, frame_obj, img_path: str):
        """在已展开的发布设置面板中上传封面图"""
        p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("[%s] 封面图不存在: %s", self.channel_name, img_path)
            return

        try:
            # 开启"自定义封面"开关 — 优先点击 switch 包装元素，fallback 到 input
            switch_on = False
            try:
                # 方式1：找 switch 组件的包装元素点击
                frame_obj.evaluate("""() => {
                    const labels = Array.from(document.querySelectorAll('label, .form-item-label, .form-item, .setting-item'));
                    const coverLabel = labels.find(l => l.textContent.includes('自定义封面'));
                    if (coverLabel) {
                        const parent = coverLabel.closest('.form-item, .setting-item, div');
                        // 先尝试找已渲染的 switch 按钮（不是 input）
                        const switchBtn = parent ? parent.querySelector('.vui_switch, .switch, [role="switch"]') : null;
                        if (switchBtn) {
                            switchBtn.click();
                            return 'switch_clicked';
                        }
                        // fallback：找 input 并设置 checked
                        const sw = parent ? parent.querySelector('input[type="checkbox"], .vui_switch-input') : null;
                        if (sw && !sw.checked) {
                            sw.click();
                            return 'input_clicked';
                        }
                        if (sw && sw.checked) return 'already_on';
                    }
                    return 'not_found';
                }""")
                switch_on = True
            except Exception:
                logger.debug('可选步骤失败，继续执行（_do_upload_cover）', exc_info=True)
                pass
            if not switch_on:
                logger.warning("[%s] 自定义封面开关未找到", self.channel_name)
            page.wait_for_timeout(1200)

            # 点击"添加封面"虚线框
            add_cover = frame.get_by_text("添加封面")
            if add_cover.count() > 0:
                add_cover.first.click()
            else:
                frame_obj.evaluate("""() => {
                    const els = Array.from(document.querySelectorAll('*')).filter(
                        e => e.textContent && e.textContent.includes('添加封面')
                    );
                    if (els.length > 0) els[0].click();
                }""")
            page.wait_for_timeout(1500)

            # 点击"本地上传"
            local_upload = frame.get_by_text("本地上传", exact=True)
            if local_upload.count() > 0:
                local_upload.first.click()
            else:
                frame_obj.evaluate("""() => {
                    const els = Array.from(document.querySelectorAll('*')).filter(
                        e => e.textContent && e.textContent.includes('本地上传')
                    );
                    if (els.length > 0) els[0].click();
                }""")
            page.wait_for_timeout(1000)

            # file chooser：先尝试 expect_file_chooser + JS click input，失败则直接 set_input_files
            file_set = False
            try:
                with page.expect_file_chooser(timeout=8000) as fc_info:
                    frame_obj.evaluate("""() => {
                        const inputs = document.querySelectorAll('input[type="file"]');
                        if (inputs.length > 0) inputs[inputs.length - 1].click();
                    }""")
                fc = fc_info.value
                fc.set_files(str(p.resolve()))
                file_set = True
                logger.info("[%s] 封面图通过 file chooser 已设置", self.channel_name)
            except Exception:
                logger.warning("[%s] file chooser 超时，尝试直接 set_input_files", self.channel_name)
                try:
                    file_input = frame.locator('input[type="file"]')
                    if file_input.count() > 0:
                        file_input.first.set_files(str(p.resolve()))
                        file_set = True
                        logger.info("[%s] 封面图通过 set_input_files 已设置", self.channel_name)
                except Exception as e2:
                    logger.warning("[%s] set_input_files 也失败: %s", self.channel_name, e2)

            if file_set:
                page.wait_for_timeout(4000)
                # 点击裁剪/确认按钮
                for btn_text in ["确定", "确认", "完成"]:
                    try:
                        btn = frame.get_by_role("button", name=btn_text)
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click(timeout=3000)
                            logger.info("[%s] 封面%s已点击", self.channel_name, btn_text)
                            page.wait_for_timeout(1500)
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_do_upload_cover）', exc_info=True)
                        continue

            save_screenshot(page, "bilibili_cover_final")
        except Exception as e:
            logger.warning("[%s] 封面上传失败: %s", self.channel_name, e)

    def _upload_body_images(self, frame, page, cover_images: list[str], image_doi_map: dict | None = None):
        """在 B站 iframe 编辑器中上传正文图片。

        核心策略：Playwright 真实鼠标 click 设置 ProseMirror 光标 → End 键移到段落末尾
        → ClipboardEvent paste 插入图片 → 清理占位符残留。

        关键发现：
        1. ProseMirror 只响应真实鼠标事件的选区同步；JS dispatchEvent 模拟 click 无效
        2. click 只设置 caret（不创建 text selection），paste 在光标处插入但不替换文本
        3. End 键将光标移到段落末尾，使图片插入到占位符段落之后（正确位置）
        4. paste 后占位符被切成两半，需分别清理 [图片: 左半和 配图] 右半，跳过完整占位符
        5. 唯一可行的图片插入方式是 ClipboardEvent('paste') + base64 File → DataTransfer
        """
        if not cover_images or len(cover_images) <= 1:
            return

        frame_obj = page.frame(url="*read-editor*")
        if frame_obj is None:
            frame_obj = page.frame(name="113")
        if frame_obj is None:
            logger.warning("[%s] iframe Frame 对象未找到", self.channel_name)
            return

        # 获取 iframe 元素在 page 中的位置，用于坐标转换
        iframe_locator = page.locator("iframe[src*='york/read-editor']").first
        if iframe_locator.count() == 0:
            iframe_locator = page.locator("iframe[src*='read-editor']").first
        if iframe_locator.count() == 0:
            iframe_locator = page.locator("iframe").first

        import base64
        body_paths = list(cover_images[1:])

        for idx, path in enumerate(body_paths):
            resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
            if not resolved or not resolved.exists():
                logger.warning("[%s] body image not found: %s", self.channel_name, path)
                continue

            p = Path(resolved)
            with open(p, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode("utf-8")
            mime_map = {
                ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
            }
            mime = mime_map.get(p.suffix.lower(), "image/png")

            try:
                # ============================================================
                # 最终策略：Playwright 单击设置光标 → 零延迟立即 evaluate paste。
                # 核心假设：ProseMirror 在 click 后的下一个事件循环前还没
                # 来得及重置选区，此时 dispatch paste 会使用 click 位置。
                # ============================================================

                # ---- Step 1: Playwright 精确匹配占位符段落 ----
                ph_locator = frame_obj.get_by_text("[图片: 论文配图]", exact=False).first
                if ph_locator.count() == 0:
                    logger.warning("[%s] body image #%d placeholder locator not found", self.channel_name, idx)
                    continue

                # ---- Step 2: 获取 bbox 并单击占位符段落中心 ----
                bbox = ph_locator.bounding_box()
                if not bbox:
                    logger.warning("[%s] body image #%d placeholder bbox not found", self.channel_name, idx)
                    continue

                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2
                page.mouse.click(cx, cy)
                # 光标移到段落末尾，使图片插入到占位符段落之后（而非开头）
                page.keyboard.press('End')
                page.wait_for_timeout(50)
                # 关键：零延迟，立即 paste

                # ---- Step 3: 立即 evaluate paste + 清理当前占位符残留 ----
                result = frame_obj.evaluate(
                    """({b64, mime, name}) => {
                        const editor = document.querySelector('[contenteditable="true"]');
                        if (!editor) return {success: false, error: 'no editor'};

                        // 3a) paste
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
                        Object.defineProperty(dt, 'types', {
                            value: ['Files'],
                            writable: false,
                            configurable: true,
                        });

                        const pasteEvent = new ClipboardEvent('paste', {
                            clipboardData: dt,
                            bubbles: true,
                            cancelable: true,
                        });
                        editor.dispatchEvent(pasteEvent);

                        // 3b) 清理当前占位符残留（paste 把占位符切成两半）
                        // 策略：分别清理 [图片: 开头的左半部分 和 配图] 结尾的右半部分
                        // 注意：跳过完整的 [图片: 论文配图]，那是下一个要处理的占位符
                        let cleaned = 0;

                        // 清理左半部分 [图片:...
                        const walker1 = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
                        let node1;
                        while (node1 = walker1.nextNode()) {
                            const text = node1.textContent || '';
                            if (text.includes('[图片: 论文配图]')) continue; // 跳过完整占位符
                            const idx = text.indexOf('[图片:');
                            if (idx !== -1) {
                                const range = document.createRange();
                                range.setStart(node1, idx);
                                range.setEnd(node1, text.length);
                                range.deleteContents();
                                cleaned++;
                                break;
                            }
                        }

                        // 清理右半部分 ...配图]
                        const walker2 = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
                        let node2;
                        while (node2 = walker2.nextNode()) {
                            const text = node2.textContent || '';
                            if (text.includes('[图片: 论文配图]')) continue; // 跳过完整占位符
                            const idx = text.indexOf('配图]');
                            if (idx !== -1) {
                                const range = document.createRange();
                                range.setStart(node2, 0);
                                range.setEnd(node2, idx + 3);
                                range.deleteContents();
                                cleaned++;
                                break;
                            }
                        }

                        // 删除空段落
                        editor.querySelectorAll('p, div, blockquote').forEach(el => {
                            if (el.textContent.trim().length === 0 && el.children.length === 0) {
                                el.remove();
                            }
                        });
                        if (cleaned > 0) {
                            editor.dispatchEvent(new Event('input', {bubbles: true}));
                        }

                        return {success: true, cleaned};
                    }""",
                    {"b64": b64_data, "mime": mime, "name": p.name},
                )

                if isinstance(result, dict) and not result.get("success"):
                    logger.warning("[%s] body image #%d paste failed: %s", self.channel_name, idx, result.get("error"))
                    continue

                page.wait_for_timeout(5000)
                logger.info("[%s] body image #%d pasted and placeholder cleaned", self.channel_name, idx)
            except Exception as e:
                logger.warning("[%s] body image #%d exception: %s", self.channel_name, idx, e)

        # 兜底清理残留占位符（包括完整和部分残留）
        try:
            leftover = frame_obj.evaluate("""() => {
                const editor = document.querySelector('[contenteditable="true"]');
                if (!editor) return 0;
                const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null, false);
                let node;
                let count = 0;
                while (node = walker.nextNode()) {
                    const text = node.textContent || '';
                    if (text.length === 0) continue;
                    let changed = false;
                    // 清理完整的占位符
                    if (text.includes('[图片: 论文配图]')) {
                        const idx = text.indexOf('[图片: 论文配图]');
                        const endIdx = idx + '[图片: 论文配图]'.length;
                        if (endIdx <= text.length) {
                            const range = document.createRange();
                            range.setStart(node, idx);
                            range.setEnd(node, endIdx);
                            range.deleteContents();
                            changed = true;
                            count++;
                        }
                    }
                    // 清理部分残留（如 [图片: 论 或 文配图]）
                    else if (text.includes('[图片:') && text.includes('配图]')) {
                        const start = text.indexOf('[图片:');
                        const end = text.indexOf('配图]') + 3;
                        if (start < end && end <= text.length) {
                            const range = document.createRange();
                            range.setStart(node, start);
                            range.setEnd(node, end);
                            range.deleteContents();
                            changed = true;
                            count++;
                        }
                    } else if (text.includes('[图片:')) {
                        const start = text.indexOf('[图片:');
                        if (start < text.length) {
                            const range = document.createRange();
                            range.setStart(node, start);
                            range.setEnd(node, text.length);
                            range.deleteContents();
                            changed = true;
                            count++;
                        }
                    } else if (text.includes('配图]')) {
                        const end = text.indexOf('配图]') + 3;
                        if (end <= text.length) {
                            const range = document.createRange();
                            range.setStart(node, 0);
                            range.setEnd(node, end);
                            range.deleteContents();
                            changed = true;
                            count++;
                        }
                    }
                    if (changed) {
                        editor.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                }
                return count;
            }""")
            if leftover:
                logger.info("[%s] 兜底清理残留占位符: %d 个", self.channel_name, leftover)
        except Exception as e:
            logger.warning("[%s] 兜底清理占位符失败: %s", self.channel_name, e)

    def _paste_image_in_frame(self, frame_obj, image_path: str, use_selection: bool = True) -> bool:
        """在 iframe 编辑器内通过 ClipboardEvent('paste') 上传单张图片"""
        p = Path(image_path)
        if not p.exists():
            return False

        with open(p, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")

        ext = p.suffix.lower()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
        }
        mime = mime_map.get(ext, "image/png")

        result = frame_obj.evaluate(
            """({b64, mime, name, useSelection}) => {
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

                        let target;
                        if (useSelection) {
                            const sel = window.getSelection();
                            if (sel.rangeCount === 0) {
                                resolve({success: false, error: 'no selection'});
                                return;
                            }
                            const range = sel.getRangeAt(0);
                            target = range.commonAncestorContainer;
                            if (target.nodeType === Node.TEXT_NODE) {
                                target = target.parentElement;
                            }
                            while (target && target.getAttribute && target.getAttribute('contenteditable') !== 'true') {
                                target = target.parentElement;
                            }
                        } else {
                            target = document.activeElement;
                            if (!target || target === document.body) {
                                const selectors = [
                                    '.ProseMirror', '[contenteditable="true"]',
                                    '.editor__inner', 'textarea',
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
                            if (document.activeElement !== target) {
                                target.focus({preventScroll: true});
                            }
                        }

                        if (!target) {
                            resolve({success: false, error: 'no editor target'});
                            return;
                        }

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
            }""",
            {"b64": b64_data, "mime": mime, "name": p.name, "useSelection": use_selection},
        )
        if result and result.get("success"):
            logger.info("[%s] Pasted image to editor, target=%s", self.channel_name, result.get("target"))
            return True
        logger.warning("[%s] Paste image failed: %s", self.channel_name, result.get("error") if result else "unknown")
        return False

    def _extract_url_from_manage_page(self, page) -> Optional[str]:
        """跳转到 B站稿件管理页，提取最新一篇文章的链接。

        作为 _click_publish 中 URL 提取的兜底方案。
        稿件管理页 URL: https://member.bilibili.com/platform/upload/text/manage
        """
        try:
            logger.info("[%s] 兜底：跳转到稿件管理页提取文章链接...", self.channel_name)
            page.goto("https://member.bilibili.com/platform/upload/text/manage",
                      wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            save_screenshot(page, "bilibili_manage_page")

            # 提取页面上第一个 read/cv 链接（即最新发布的文章）
            url = page.evaluate("""() => {
                // 查找所有包含 read/cv 的链接
                const links = document.querySelectorAll('a[href*="read/cv"]');
                for (const link of links) {
                    const href = link.getAttribute('href');
                    if (href) {
                        // 确保是完整 URL
                        if (href.startsWith('http')) return href;
                        if (href.startsWith('//')) return 'https:' + href;
                        return 'https://www.bilibili.com' + href;
                    }
                }
                // fallback: 搜索页面文本中的 cv 编号
                const text = document.body.innerText || '';
                const m = text.match(/cv\\d+/);
                if (m) return 'https://www.bilibili.com/read/' + m[0];
                return null;
            }""")
            if url:
                logger.info("[%s] 从管理页提取到文章链接: %s", self.channel_name, url)
            else:
                logger.warning("[%s] 管理页未找到文章链接", self.channel_name)
            return url
        except Exception as e:
            logger.warning("[%s] 从管理页提取链接失败: %s", self.channel_name, e)
            return None

    def _click_publish(self, frame, page) -> Optional[str]:
        """点击发布按钮，返回文章 URL；遇到投稿上限返回 __QUOTA_EXCEEDED__"""
        # 先尝试关闭"发布设置"弹窗（避免遮挡）
        try:
            close_btn = frame.locator(".vui_dialog--close, [class*='dialog'] [class*='close'], button[class*='close']")
            if close_btn.count() > 0:
                for i in range(close_btn.count()):
                    if close_btn.nth(i).is_visible():
                        close_btn.nth(i).click(timeout=3000)
                        logger.info("[%s] 关闭弹窗", self.channel_name)
                        page.wait_for_timeout(500)
                        break
        except Exception:
            logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
            pass

        # 多种方式尝试点击发布按钮
        clicked = False

        # 方式1: 在 iframe (frame) 内查找
        for btn_name in ["发布", "提交文章"]:
            if clicked:
                break
            try:
                frame.get_by_role("button", name=btn_name).click(timeout=10000)
                logger.info("[%s] 发布按钮已点击 (iframe get_by_role '%s')", self.channel_name, btn_name)
                clicked = True
            except Exception as e:
                logger.info("[%s] iframe get_by_role('%s') 失败: %s", self.channel_name, btn_name, e)

        # 方式1b: 在外层 page 上查找（发布按钮可能不在 iframe 内）
        if not clicked:
            for btn_name in ["发布", "提交文章"]:
                if clicked:
                    break
                try:
                    page.get_by_role("button", name=btn_name).click(timeout=10000)
                    logger.info("[%s] 发布按钮已点击 (page get_by_role '%s')", self.channel_name, btn_name)
                    clicked = True
                except Exception as e:
                    logger.info("[%s] page get_by_role('%s') 失败: %s", self.channel_name, btn_name, e)

        # 方式2: 在 iframe 内 force 点击
        if not clicked:
            try:
                btns = frame.locator("button").all()
                for btn in btns:
                    try:
                        text = btn.inner_text()
                        if "发布" in text and btn.is_visible():
                            btn.click(force=True, timeout=5000)
                            logger.info("[%s] 发布按钮已点击 (iframe force, text='%s')", self.channel_name, text)
                            clicked = True
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                        continue
            except Exception as e:
                logger.info("[%s] iframe force 点击失败: %s", self.channel_name, e)

        # 方式2b: 在外层 page 上 force 点击
        if not clicked:
            try:
                btns = page.locator("button").all()
                for btn in btns:
                    try:
                        text = btn.inner_text()
                        if "发布" in text and btn.is_visible():
                            btn.click(force=True, timeout=5000)
                            logger.info("[%s] 发布按钮已点击 (page force, text='%s')", self.channel_name, text)
                            clicked = True
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                        continue
            except Exception as e:
                logger.info("[%s] page force 点击失败: %s", self.channel_name, e)

        # 方式3: JS 在 iframe 内点击
        if not clicked:
            try:
                result = page.evaluate("""() => {
                    const iframe = document.querySelector('iframe[src*="read-editor"], iframe[src*="read-draft"]');
                    if (!iframe) return 'no iframe';
                    const doc = iframe.contentDocument;
                    const btns = Array.from(doc.querySelectorAll('button'));
                    const pub = btns.find(b => b.textContent.includes('发布') && b.offsetParent !== null);
                    if (pub) { pub.click(); return 'clicked 发布: ' + pub.textContent.trim(); }
                    const submit = btns.find(b => b.textContent.includes('提交文章') && b.offsetParent !== null);
                    if (submit) { submit.click(); return 'clicked 提交文章: ' + submit.textContent.trim(); }
                    return 'not found, buttons: ' + btns.filter(b => b.offsetParent !== null).map(b => b.textContent.trim()).slice(0,10).join(' | ');
                }""")
                logger.info("[%s] JS iframe 点击结果: %s", self.channel_name, result)
                if "clicked" in result:
                    clicked = True
            except Exception as e:
                logger.info("[%s] JS iframe 点击失败: %s", self.channel_name, e)

        # 方式3b: JS 在外层 page 上点击
        if not clicked:
            try:
                result = page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const pub = btns.find(b => b.textContent.includes('发布') && b.offsetParent !== null);
                    if (pub) { pub.click(); return 'clicked 发布: ' + pub.textContent.trim(); }
                    const submit = btns.find(b => b.textContent.includes('提交文章') && b.offsetParent !== null);
                    if (submit) { submit.click(); return 'clicked 提交文章: ' + submit.textContent.trim(); }
                    return 'not found, buttons: ' + btns.filter(b => b.offsetParent !== null).map(b => b.textContent.trim()).slice(0,10).join(' | ');
                }""")
                logger.info("[%s] JS page 点击结果: %s", self.channel_name, result)
                if "clicked" in result:
                    clicked = True
            except Exception as e:
                logger.info("[%s] JS page 点击失败: %s", self.channel_name, e)

        if not clicked:
            logger.warning("[%s] 所有发布按钮点击方式均失败", self.channel_name)
            save_screenshot(page, "bilibili_publish_btn_fail")
            return None

        page.wait_for_timeout(4000)
        save_screenshot(page, "bilibili_after_publish_click")

        # ── 检测并处理"达到当日投稿上限"弹窗 ──
        quota_exceeded = False
        try:
            for locator in [page, frame]:
                try:
                    dialog = locator.locator("text=达到当日投稿上限")
                    if dialog.count() > 0 and dialog.first.is_visible():
                        quota_exceeded = True
                        logger.warning("[%s] 检测到当日投稿上限弹窗", self.channel_name)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                    continue

            if quota_exceeded:
                # 点击弹窗确认按钮
                confirmed = False
                for btn_text in ["确认", "确定", "我知道了"]:
                    for locator in [page, frame]:
                        try:
                            btn = locator.get_by_role("button", name=btn_text)
                            if btn.count() > 0 and btn.first.is_visible():
                                btn.first.click(timeout=5000)
                                logger.info("[%s] 已点击弹窗按钮 '%s'", self.channel_name, btn_text)
                                confirmed = True
                                break
                        except Exception:
                            logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                            continue
                    if confirmed:
                        break
                page.wait_for_timeout(2000)
                save_screenshot(page, "bilibili_quota_dialog_closed")

                # 尝试保存为草稿
                draft_saved = False
                for locator in [page, frame]:
                    try:
                        btn = locator.get_by_role("button", name="保存为草稿")
                        if btn.count() > 0 and btn.first.is_visible():
                            btn.first.click(timeout=5000)
                            draft_saved = True
                            logger.info("[%s] 已点击保存为草稿", self.channel_name)
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                        continue
                if draft_saved:
                    page.wait_for_timeout(2000)
                    save_screenshot(page, "bilibili_draft_saved")
                return "__QUOTA_EXCEEDED__"
        except Exception as e:
            logger.info("[%s] 检测投稿上限弹窗时出错: %s", self.channel_name, e)

        # ── 提取文章链接 ──
        # 等待更长的时间让页面状态稳定
        page.wait_for_timeout(3000)

        # 1) 检测 iframe 或 page 中的成功提示与链接
        for locator in [frame, page]:
            try:
                text = (locator.locator("body").inner_text(timeout=5000) or "")
                # 查找 read/cv 链接
                import re
                m = re.search(r'(https?://www\.bilibili\.com/read/cv\d+)', text)
                if m:
                    url = m.group(1)
                    logger.info("[%s] 从页面文本提取到文章链接: %s", self.channel_name, url)
                    return url
            except Exception:
                logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                pass

        # 2) 尝试点击"点击查看"打开新标签页
        try:
            for locator in [frame, page]:
                try:
                    view_link = locator.get_by_text("点击查看")
                    if view_link.count() > 0 and view_link.first.is_visible():
                        view_link.first.click(timeout=5000)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
                    continue
            page.wait_for_timeout(3000)
            for pg in page.context.pages:
                if "bilibili.com/read" in pg.url:
                    logger.info("[%s] 文章链接: %s", self.channel_name, pg.url)
                    return pg.url
        except Exception as e:
            logger.info("[%s] 未找到'点击查看': %s", self.channel_name, e)

        # 3) 检查当前页面 URL 是否跳转
        current = page.url
        if "bilibili.com" in current and "read" in current:
            return current

        # 4) 从 iframe 的 location 获取
        try:
            iframe_url = page.evaluate("""() => {
                const iframe = document.querySelector('iframe[src*="read-editor"], iframe[src*="read-draft"]');
                return iframe ? iframe.contentWindow.location.href : null;
            }""")
            if iframe_url and "read" in iframe_url:
                return iframe_url
        except Exception:
            logger.debug('可选步骤失败，继续执行（_click_publish）', exc_info=True)
            pass

        # 5) 兜底：跳转到稿件管理页，提取最新文章的链接
        url = self._extract_url_from_manage_page(page)
        if url:
            return url

        return None

    # ------------------------------------------------------------------
    # BasePublisher 接口
    # ------------------------------------------------------------------
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
                error="哔哩哔哩 cookie 未配置",
            )

        image_doi_map = kwargs.get("image_doi_map") or {}
        content = self.process_body_images(content, cover_images or [], image_doi_map=image_doi_map)

        try:
            check_cancel = kwargs.get("check_cancel")
            return self._publish_with_playwright(title, content, cover_images, image_doi_map=image_doi_map, check_cancel=check_cancel, cookie=cookie)
        except Exception as e:
            logger.error("[%s] 发布失败: %s", self.channel_name, e)
            logger.error(traceback.format_exc())
            return PublishResult(
                success=False, channel=self.channel_name, error="发布失败，请稍后重试"
            )
