"""搜狐号 publisher — 参考 toutiao-auto-publisher 项目

关键差异：
- 入口使用 v4 内容管理页面
- Quill 编辑器用 keyboard.type 模拟输入（innerHTML 会被清空）
- 标题用 Playwright fill（而非 JS value）
- 发布按钮使用精确选择器列表
"""
from __future__ import annotations
import logging
import os
import random
import traceback
from pathlib import Path
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import parse_cookie_string, launch_browser_context, save_screenshot
from tools import storage

logger = logging.getLogger(__name__)

_CONTENT_MANAGE_URL = "https://mp.sohu.com/mpfe/v4/contentManagement/first/page"

# 发布按钮选择器（从参考项目移植，同步 API 版本）
PUBLISH_BTN_SELECTORS = [
    "li.publish-report-btn.active.positive-button",
    "li.publish-report-btn.positive-button",
    "li.publish-report-btn.active",
    ".publish-report-btn.positive-button",
    ".publish-report-btn",
    ".bottom-button-outer-absolute li.positive-button",
    ".item-btn-wrapper .positive-button",
]


class SohuPublisher(BasePublisher):
    channel_name = "sohu"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    def process_body_images(
        self,
        content: str,
        image_paths: list[str],
        image_doi_map: dict[str, str] | None = None,
    ) -> str:
        """搜狐号：不在正文中预置 [图片: 论文配图] 占位符，只保留原始内容。
        图片和引用说明由 _upload_body_images 在编辑器末尾动态插入。"""
        # 占位符和图注在上传阶段动态处理，此处不追加任何额外文本
        return content

    def _upload_body_images(self, page, cover_images: list[str], image_doi_map: dict | None = None) -> dict[str, str]:
        """在搜狐 Quill 编辑器中上传正文图片，并在每张图片后追加引用说明。

        流程：定位光标到编辑器末尾 → ql-image 上传 → 确认插入 → 输入引用文本。
        """
        if not cover_images or len(cover_images) <= 1:
            return {}
        results: dict[str, str] = {}
        for path in cover_images[1:]:
            resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
            if not resolved or not resolved.exists():
                logger.warning("搜狐 body image not found: %s", path)
                continue
            try:
                # 把光标移到编辑器末尾，确保图片顺序插入
                page.keyboard.press("Control+End")
                page.wait_for_timeout(300)

                url = self._upload_single_body_image_sohu(page, str(resolved), skip_editor_click=True)
                if url:
                    results[path] = url
                    logger.info("搜狐 body image uploaded: %s -> %s", path, url)
                    # 在图片下方输入引用说明
                    doi = (image_doi_map or {}).get(path)
                    if doi:
                        page.wait_for_timeout(500)
                        page.keyboard.type(f"\n（引自：{doi}）", delay=random.randint(5, 15))
                        page.wait_for_timeout(500)
                else:
                    logger.warning("搜狐 body image upload failed: %s", path)
            except Exception as e:
                logger.warning("搜狐 body image upload exception for %s: %s", path, e)
        return results

    def _upload_single_body_image_sohu(self, page, image_path: str, skip_editor_click: bool = False) -> str | None:
        """搜狐定制单图上传：走官方 UI 流程（ql-image → modal → upload → confirm）。"""
        try:
            # 0) 聚焦编辑器（外部已定位光标时可跳过）
            if not skip_editor_click:
                editor = page.locator('.ql-editor, [contenteditable="true"]').first
                editor.click()
                page.wait_for_timeout(300)

            # 1) 点击 ql-image
            img_btn = page.locator('button.ql-image').first
            if img_btn.count() == 0:
                logger.warning("搜狐 ql-image 按钮未找到")
                return None
            img_btn.click()
            page.wait_for_timeout(1500)

            # 2) 找到 modal 内的 file input
            file_input = page.locator('input[type="file"]').first
            if file_input.count() == 0:
                logger.warning("搜狐 Modal file input 未找到")
                return None
            file_input.evaluate("el => { el.style.display='block'; el.style.visibility='visible'; }")
            file_input.set_input_files(image_path)
            logger.info("搜狐图片已选择: %s", image_path)
            page.wait_for_timeout(4000)

            # 3) 点击"确定"插入图片
            confirm_btn = page.locator('p.button.positive-button').first
            if confirm_btn.count() > 0 and confirm_btn.is_visible():
                confirm_btn.click()
            else:
                page.evaluate(
                    "() => { const p = document.querySelector('p.button.positive-button');"
                    "if (p) p.click(); }"
                )
            page.wait_for_timeout(3000)

            # 4) 提取最后插入的图片 URL（避免重复返回第一张）
            url = page.evaluate(
                "() => { const imgs = document.querySelectorAll('.ql-editor img');"
                "return imgs.length > 0 ? imgs[imgs.length - 1].src : null; }"
            )
            return url if isinstance(url, str) and url.startswith("http") else None
        except Exception as e:
            logger.warning("搜狐单图上传异常: %s", e)
            return None

    def _replace_image_placeholders(self, page, url_map: dict[str, str]):
        """在 Quill 编辑器中替换 [图片: 论文配图] 占位符为实际图片"""
        if not url_map:
            return
        for local_path, url in url_map.items():
            try:
                page.evaluate("""(localPath, remoteUrl) => {
                    const editors = document.querySelectorAll('.ql-editor, [contenteditable="true"]');
                    for (const ed of editors) {
                        const text = ed.innerText || '';
                        const placeholder = '[图片: 论文配图]';
                        if (text.includes(placeholder)) {
                            const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT, null, false);
                            let node;
                            while (node = walker.nextNode()) {
                                if (node.textContent.includes(placeholder)) {
                                    const img = document.createElement('img');
                                    img.src = remoteUrl;
                                    img.alt = '论文配图';
                                    img.style.maxWidth = '100%';
                                    const range = document.createRange();
                                    range.selectNode(node);
                                    range.deleteContents();
                                    range.insertNode(img);
                                    ed.dispatchEvent(new Event('input', { bubbles: true }));
                                    return {ok: true};
                                }
                            }
                        }
                    }
                    return {ok: false};
                }""", local_path, url)
            except Exception as e:
                logger.debug("搜狐 replace placeholder failed for %s: %s", local_path, e)

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
                error="Sohu cookie not configured",
            )

        image_doi_map = kwargs.get("image_doi_map") or {}
        # 追加剩余图片占位符到正文（搜狐 Quill 编辑器后续需实现实际上传）
        content = self.process_body_images(content, cover_images or [], image_doi_map=image_doi_map)

        check_cancel = kwargs.get("check_cancel")

        try:
            return self._publish_with_playwright(title, content, cover_images, image_doi_map=image_doi_map, check_cancel=check_cancel)
        except Exception as e:
            logger.error("搜狐号发布失败: %s", e)
            logger.error(traceback.format_exc())
            return PublishResult(
                success=False, channel=self.channel_name, error="发布失败，请稍后重试"
            )

    def _publish_with_playwright(
        self,
        title: str,
        content: str,
        cover_images: Optional[List[str]] = None,
        image_doi_map: dict | None = None,
        check_cancel=None,
    ) -> PublishResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(self.cookie, ".sohu.com"))
            page = context.new_page()
            try:

                # 取消检查点 1: 浏览器启动后
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 1) 打开内容管理页面
                logger.info("打开搜狐号内容管理页面: %s", _CONTENT_MANAGE_URL)
                page.goto(_CONTENT_MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                save_screenshot(page, "sohu_content_mgmt")

                url = page.url
                if "login" in url.lower():
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="搜狐号 Cookie 已失效",
                    )

                # 2) 点击"发布内容"按钮
                logger.info("点击发布内容按钮...")
                clicked = page.evaluate("""() => {
                    // 参考项目选择器
                    const el = document.querySelector("#menu-ic_publish > div > div > span");
                    if (el) { el.click(); return "menu-ic_publish"; }
                    // fallback: 文本匹配
                    const all = Array.from(document.querySelectorAll('a, button, span, div'));
                    const t = all.find(e => e.textContent.trim() === '发布内容');
                    if (t) { t.click(); return "text_match"; }
                    return false;
                }""")
                if clicked:
                    logger.info("已点击发布内容按钮 (%s)", clicked)
                else:
                    logger.warning("未找到发布内容按钮，尝试直接访问编辑器...")
                page.wait_for_timeout(3000)
                save_screenshot(page, "sohu_editor_opened")

                # 取消检查点 2: 编辑器打开后
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 3) 填入标题
                logger.info("填入标题...")
                self._fill_title(page, title)
                save_screenshot(page, "sohu_title_filled")

                # 取消检查点 3: 标题填入后
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 4) 填入正文（Quill 编辑器用 keyboard.type）
                logger.info("填入正文...")
                self._fill_content(page, content)
                save_screenshot(page, "sohu_content_filled")

                # 4.5) 上传正文配图并替换占位符
                if cover_images and len(cover_images) > 1:
                    logger.info("Uploading 搜狐 body images...")
                    url_map = self._upload_body_images(page, cover_images, image_doi_map)
                    if url_map:
                        self._replace_image_placeholders(page, url_map)
                        save_screenshot(page, "sohu_body_images_replaced")

                # 取消检查点 4: 正文填入后
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 5) 上传封面（如有）
                if cover_images:
                    logger.info("上传封面...")
                    cover_path = self._resolve_cover_path(cover_images[0])
                    if cover_path:
                        self._upload_cover(page, cover_path)
                        save_screenshot(page, "sohu_cover_uploaded")

                # 取消检查点 5: 封面上传后
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 6) 点击发布按钮 + 处理确认弹窗
                logger.info("点击发布按钮...")
                publish_ok, publish_error = self._click_publish_and_confirm(page)
                if not publish_ok:
                    save_screenshot(page, "sohu_publish_btn_failed")
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error=publish_error or "未找到或未能点击发布按钮",
                    )

                # 等待发布完成
                page.wait_for_timeout(5000)
                current_url = page.url
                save_screenshot(page, "sohu_after_publish")

                browser.close()

                # 判断是否成功：从编辑器页面离开即视为成功（通常会跳转到内容管理页）
                success = "addarticle" not in current_url
                return PublishResult(
                    success=success,
                    channel=self.channel_name,
                    url=current_url if "sohu.com" in current_url else None,
                    raw={"current_url": current_url},
                )

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
    def _fill_title(self, page, title: str):
        """使用 Playwright fill 填入标题（参考项目方式）"""
        title = title[:30]
        # 参考项目选择器
        selectors = [
            "#app > div.add_content-wrap > div > div > div:nth-child(1) > div > div.publish-title > input[type=text]",
            "input[type=text].publish-title-input",
            "div.publish-title > input[type=text]",
            "input[placeholder*='标题']",
        ]
        for sel in selectors:
            try:
                inp = page.locator(sel).first
                if inp.count() > 0 and inp.is_visible():
                    inp.click()
                    page.wait_for_timeout(300)
                    inp.fill(title)
                    logger.info("标题已填入 (%s)", sel)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_title）', exc_info=True)
                continue
        # fallback: JS
        page.evaluate("""(title) => {
            const inputs = Array.from(document.querySelectorAll('input[type=text]'));
            const target = inputs.find(i => (i.placeholder||'').includes('标题')) || inputs[0];
            if (target) {
                target.value = title;
                target.dispatchEvent(new Event('input', { bubbles: true }));
                target.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""", title)
        logger.info("标题已填入 (JS fallback)")
        page.wait_for_timeout(500)

    def _fill_content(self, page, content: str):
        """Quill 编辑器：先 click 聚焦，再用 keyboard.type 模拟输入（参考项目方式）"""
        # 去除 Markdown 标记，转为纯文本段落
        text = self._preprocess_content(content)

        # 1) 找到 Quill 编辑器并 click 聚焦
        quill_selectors = [
            "#editor > div.ql-editor",
            ".ql-editor",
            "[contenteditable=\"true\"].ql-editor",
        ]
        editor_found = False
        for sel in quill_selectors:
            try:
                ed = page.locator(sel).first
                if ed.count() > 0 and ed.is_visible():
                    ed.click()
                    page.wait_for_timeout(500)
                    editor_found = True
                    logger.info("Quill 编辑器已聚焦 (%s)", sel)
                    break
            except Exception:
                logger.debug('可选步骤失败，继续执行（_fill_content）', exc_info=True)
                continue

        if not editor_found:
            # fallback: 找最大 contenteditable
            result = page.evaluate("""() => {
                const eds = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                if (eds.length === 0) return false;
                const target = eds.reduce((a,b) => (a.innerText||'').length > (b.innerText||'').length ? a : b);
                target.click();
                target.focus();
                return true;
            }""")
            if result:
                editor_found = True
                logger.info("Quill 编辑器已聚焦 (JS fallback)")
                page.wait_for_timeout(500)

        if not editor_found:
            logger.warning("未找到正文编辑器")
            return

        # 2) 清空已有内容
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(300)

        # 3) 用 keyboard.type 逐字输入（参考项目方式，模拟真实打字）
        page.keyboard.type(text, delay=random.randint(10, 30))
        logger.info("正文已填入 (keyboard.type)")
        page.wait_for_timeout(2000)

    def _preprocess_content(self, content: str) -> str:
        """将 Markdown 转为适合 keyboard.type 的纯文本段落（保留标题原样）"""
        from publisher._content_preprocessor import ContentPreprocessor
        return ContentPreprocessor("rich_text").process(content)

    def _resolve_cover_path(self, img_path: str) -> Path | None:
        p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("封面图不存在: %s", img_path)
            return None
        return p.resolve()

    def _upload_cover(self, page, img_path: Path):
        """搜狐号封面上传：虚线框 → 弹窗(三选项) → 本地上传 → 上传图片按钮 → file input → 确定

        用户描述的关键步骤：
        1. 点击封面虚线框"+ 上传图片"
        2. 弹窗中有三个选项，点击"本地上传"
        3. 内容区域中再点击"上传图片"按钮（唤起文件选择）
        4. 确定
        """
        try:
            # 1. 滚动到底部，让封面区域进入视口
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            save_screenshot(page, "sohu_cover_scroll")

            # 2. 点击封面虚线框上传按钮
            logger.info("点击封面虚线框...")
            cover_clicked = False
            for sel in ["div.upload-file.mp-upload", "div.upload-file"]:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        cover_clicked = True
                        logger.info("已点击封面按钮 (%s)", sel)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    continue

            if not cover_clicked:
                # fallback: 通过文本定位"上传图片"
                try:
                    fb = page.get_by_text("上传图片", exact=False).first
                    if fb.count() > 0 and fb.is_visible():
                        fb.click()
                        cover_clicked = True
                        logger.info("已点击封面按钮 (文本定位)")
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    pass

            if not cover_clicked:
                logger.warning("未找到封面按钮")
                return

            page.wait_for_timeout(2000)
            save_screenshot(page, "sohu_cover_dialog")

            # 3. 弹窗中点击"本地上传"标签（三个选项之一）
            try:
                local_tab = page.get_by_text("本地上传", exact=True).first
                if local_tab.count() > 0 and local_tab.is_visible():
                    local_tab.click()
                    logger.info("已点击'本地上传'标签")
                    page.wait_for_timeout(1000)
                    save_screenshot(page, "sohu_cover_local_tab")
                else:
                    # fallback: 非精确匹配
                    local_tab2 = page.locator("text=本地上传").first
                    if local_tab2.count() > 0 and local_tab2.is_visible():
                        local_tab2.click()
                        logger.info("已点击'本地上传'标签 (fallback)")
                        page.wait_for_timeout(1000)
                        save_screenshot(page, "sohu_cover_local_tab2")
            except Exception as e:
                logger.debug("点击'本地上传'标签失败: %s", e)

            # 4. 【关键】本地上传内容区域中，再点击"上传图片"按钮
            #    这是之前遗漏的步骤——点击标签后，内容区域里还有一个按钮来唤起文件选择
            logger.info("点击内容区域中的'上传图片'按钮...")
            try:
                inner_btn = page.get_by_text("上传图片", exact=False).first
                if inner_btn.count() > 0 and inner_btn.is_visible():
                    inner_btn.click()
                    logger.info("已点击内容区域'上传图片'按钮")
                    page.wait_for_timeout(1000)
                    save_screenshot(page, "sohu_cover_inner_upload")
            except Exception as e:
                logger.debug("点击内容区域'上传图片'失败: %s", e)

            # 5. 找弹窗内的 file input，设置文件并触发事件
            try:
                file_inputs = page.locator('input[type="file"]')
                total = file_inputs.count()
                logger.info("弹窗内共有 %d 个 file input", total)
                for i in range(total):
                    try:
                        inp = file_inputs.nth(i)
                        inp.evaluate("""el => {
                            el.style.display = 'block';
                            el.style.visibility = 'visible';
                            el.style.opacity = '1';
                            el.style.position = 'relative';
                            el.style.zIndex = '9999';
                        }""")
                        inp.set_input_files(str(img_path))
                        # 触发 change/input 事件让上传开始
                        inp.evaluate("""el => {
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                        }""")
                        page.wait_for_timeout(2000)
                        logger.info("封面文件已设置到 input #%d", i)
                        save_screenshot(page, "sohu_cover_file_set")
                        break
                    except Exception as e:
                        logger.debug("input #%d 失败: %s", i, e)
                        continue
            except Exception as e:
                logger.debug("查找 file input 失败: %s", e)

            # 6. 点击确定关闭弹窗 —— 必须按文本"确定"精确匹配，限定在弹窗内
            confirm_clicked = False
            page.wait_for_timeout(1500)
            save_screenshot(page, "sohu_cover_before_confirm")

            # ===== 封面弹窗确定按钮点击（关键修复）=====
            # 先通过 JS 全面探测弹窗内所有可点击元素（便于调试）
            try:
                btn_info = page.evaluate("""() => {
                    // 扩大弹窗选择器范围
                    const dlgSelectors = [
                        '.el-dialog__wrapper', '.el-dialog', '[role="dialog"]',
                        '.mp-dialog', '.cover-dialog', '[class*="dialog"]',
                        '.el-message-box__wrapper', '.el-message-box'
                    ];
                    const allDialogs = [];
                    for (const sel of dlgSelectors) {
                        document.querySelectorAll(sel).forEach(d => allDialogs.push(d));
                    }
                    // 也去重
                    const uniqueDlgs = [...new Set(allDialogs)];
                    
                    for (const dlg of uniqueDlgs) {
                        const style = window.getComputedStyle(dlg);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        // 扩大按钮选择器到所有元素
                        const allEls = Array.from(dlg.querySelectorAll('*'));
                        const clickable = allEls.filter(el => {
                            const s = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return s.display !== 'none' && rect.width > 30 && rect.height > 20;
                        });
                        return {
                            found: true,
                            dialogClass: dlg.className || '',
                            dialogTag: dlg.tagName,
                            elementCount: clickable.length,
                            buttons: clickable.slice(0, 20).map(b => ({
                                text: (b.innerText || b.textContent || '').trim().substring(0, 30),
                                tag: b.tagName,
                                className: (b.className || '').substring(0, 60),
                                rect: {x: Math.round(b.getBoundingClientRect().left), y: Math.round(b.getBoundingClientRect().top),
                                       w: Math.round(b.getBoundingClientRect().width), h: Math.round(b.getBoundingClientRect().height)}
                            }))
                        };
                    }
                    return {found: false, allDialogCount: uniqueDlgs.length};
                }""")
                logger.info("封面弹窗按钮探测: %s", btn_info)
            except Exception as e:
                logger.debug("探测弹窗按钮失败: %s", e)
                btn_info = None

            # 策略1: JS 全面搜索——在所有弹窗/遮罩层内，按文本和样式找"确定"按钮
            if not confirm_clicked:
                try:
                    js_result = page.evaluate("""() => {
                        // 1) 先找弹窗/遮罩层
                        const dlgSelectors = [
                            '.el-dialog__wrapper', '.el-dialog', '[role="dialog"]',
                            '.mp-dialog', '.cover-dialog', '[class*="dialog"]',
                            '.el-message-box__wrapper', '.el-message-box',
                            '.modal', '.overlay'
                        ];
                        const allDialogs = [];
                        for (const sel of dlgSelectors) {
                            document.querySelectorAll(sel).forEach(d => {
                                const s = window.getComputedStyle(d);
                                if (s.display !== 'none' && s.visibility !== 'hidden') allDialogs.push(d);
                            });
                        }
                        
                        // 2) 在每个弹窗内搜索所有可见元素，找"确定"
                        for (const dlg of allDialogs) {
                            const allEls = Array.from(dlg.querySelectorAll('*'));
                            // 优先：文本精确匹配 + 黄色/primary 样式
                            const candidates = allEls.filter(el => {
                                const text = (el.innerText || el.textContent || '').trim();
                                const s = window.getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                return text === '确定' && s.display !== 'none' && rect.width > 40 && rect.height > 25;
                            });
                            
                            if (candidates.length > 0) {
                                // 优先选黄色/primary样式的
                                const primary = candidates.find(el => {
                                    const s = window.getComputedStyle(el);
                                    return s.backgroundColor.includes('255') || (el.className||'').includes('primary') || (el.className||'').includes('positive');
                                });
                                const target = primary || candidates[0];
                                const rect = target.getBoundingClientRect();
                                
                                // 用 elementFromPoint 获取真实可点击元素再 click
                                const realEl = document.elementFromPoint(rect.left + rect.width/2, rect.top + rect.height/2);
                                if (realEl) {
                                    realEl.click();
                                    return {
                                        clicked: true,
                                        text: '确定',
                                        tag: target.tagName,
                                        className: target.className || '',
                                        rect: {x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)},
                                        method: 'js_dialog_text_coordinate'
                                    };
                                } else {
                                    target.click();
                                    return {
                                        clicked: true,
                                        text: '确定',
                                        tag: target.tagName,
                                        className: target.className || '',
                                        method: 'js_dialog_text_direct'
                                    };
                                }
                            }
                        }
                        
                        // 3) 终极 fallback：不限制在弹窗内，viewport 底部区域全局搜索
                        const viewportH = window.innerHeight;
                        const allBtns = Array.from(document.querySelectorAll('button, a, div, span, li'));
                        for (const btn of allBtns) {
                            const text = (btn.innerText || btn.textContent || '').trim();
                            const s = window.getComputedStyle(btn);
                            const rect = btn.getBoundingClientRect();
                            if (text === '确定' && s.display !== 'none' && rect.top > viewportH * 0.5 
                                && rect.width > 60 && rect.height > 25) {
                                btn.click();
                                return {
                                    clicked: true,
                                    text: '确定',
                                    tag: btn.tagName,
                                    className: btn.className || '',
                                    method: 'js_global_bottom_text'
                                };
                            }
                        }
                        
                        return {clicked: false, dialogCount: allDialogs.length};
                    }""")
                    logger.info("JS确定按钮结果: %s", js_result)
                    if js_result and js_result.get("clicked"):
                        confirm_clicked = True
                        page.wait_for_timeout(1500)
                        save_screenshot(page, "sohu_cover_confirmed_js")
                except Exception as e:
                    logger.debug("JS点击确定失败: %s", e)

            # 策略2: Playwright 使用全局文本定位 + 坐标点击
            if not confirm_clicked:
                try:
                    # 找页面上所有可见的"确定"，选最下面的（通常在弹窗底部）
                    all_confirms = page.locator("text=确定").all()
                    best = None
                    best_y = -1
                    for el in all_confirms:
                        try:
                            if el.is_visible():
                                box = el.bounding_box()
                                if box and box['y'] > best_y:
                                    best_y = box['y']
                                    best = el
                        except Exception:
                            logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                            continue
                    if best:
                        best.click()
                        confirm_clicked = True
                        logger.info("已点击弹窗确定 (Playwright全局底部定位)")
                        page.wait_for_timeout(1500)
                        save_screenshot(page, "sohu_cover_confirmed_pw")
                except Exception as e:
                    logger.debug("Playwright定位确定失败: %s", e)

            # 策略3: 点击弹窗右上角 × 关闭按钮（作为 fallback）
            if not confirm_clicked:
                try:
                    close_result = page.evaluate("""() => {
                        const closeIcons = document.querySelectorAll('.el-dialog__close, .el-message-box__close, [class*="close"], .mp-dialog-close');
                        for (const icon of closeIcons) {
                            const s = window.getComputedStyle(icon);
                            if (s.display !== 'none') {
                                icon.click();
                                return {clicked: true, method: 'close_icon'};
                            }
                        }
                        // fallback: 找右上角区域的小按钮
                        const allEls = Array.from(document.querySelectorAll('i, span, div'));
                        const viewportW = window.innerWidth;
                        for (const el of allEls) {
                            const rect = el.getBoundingClientRect();
                            const text = (el.innerText || '').trim();
                            if (rect.right > viewportW - 100 && rect.top < 100 && rect.width < 40 && rect.height < 40) {
                                el.click();
                                return {clicked: true, method: 'top_right_corner'};
                            }
                        }
                        return {clicked: false};
                    }""")
                    logger.info("关闭弹窗尝试: %s", close_result)
                    if close_result and close_result.get("clicked"):
                        page.wait_for_timeout(1000)
                        save_screenshot(page, "sohu_cover_closed_icon")
                except Exception as e:
                    logger.debug("关闭弹窗失败: %s", e)

            # 策略4: ESC 键关闭
            if not confirm_clicked:
                logger.warning("未能找到并点击确定按钮，尝试 ESC 关闭弹窗...")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(800)
                    save_screenshot(page, "sohu_cover_escape")
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                    pass

            # 7. 验证封面是否真正出现在页面上
            if confirm_clicked:
                page.wait_for_timeout(1000)
                save_screenshot(page, "sohu_cover_after_confirm")

                # 滚动到封面区域检查
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)

                cover_on_page = page.evaluate("""() => {
                    // 找封面区域的图片（排除弹窗内）
                    const coverSection = document.querySelector('[class*="cover"], [class*="upload-file"]');
                    if (coverSection) {
                        const imgs = coverSection.querySelectorAll('img');
                        for (const img of imgs) {
                            const rect = img.getBoundingClientRect();
                            if (rect.width > 80 && rect.height > 50) return true;
                        }
                    }
                    // fallback: 找页面底部大图（封面通常是较大的图）
                    const allImgs = document.querySelectorAll('img');
                    for (const img of allImgs) {
                        const rect = img.getBoundingClientRect();
                        const parent = img.closest('.el-dialog, .el-dialog__wrapper, [role="dialog"]');
                        if (!parent && rect.width > 120 && rect.height > 80) return true;
                    }
                    return false;
                }""")
                if cover_on_page:
                    logger.info("封面图片已确认出现在页面上")
                else:
                    logger.warning("封面图片可能未成功应用到文章")
                save_screenshot(page, "sohu_cover_final_check")
            else:
                logger.warning("未能点击确定按钮，封面可能未保存")

        except Exception as e:
            logger.warning("封面上传异常: %s", e)

    def _dismiss_toast_and_dialog(self, page):
        """关闭错误提示弹窗（如封面上传失败"添加失败"）"""
        try:
            page.evaluate("""() => {
                document.querySelectorAll('.el-message, .el-notification, .toast, [class*="toast"]').forEach(el => el.remove());
                const dialogs = document.querySelectorAll('.el-message-box, .el-dialog');
                for (const dlg of dialogs) {
                    const text = (dlg.innerText || '').trim();
                    if (text.includes('添加失败') || text.includes('上传失败')) {
                        const btns = dlg.querySelectorAll('button');
                        for (const btn of btns) {
                            if ((btn.innerText || '').trim() === '确定') {
                                btn.click();
                                break;
                            }
                        }
                    }
                }
            }""")
            page.wait_for_timeout(500)
        except Exception as e:
            logger.debug("关闭干扰弹窗: %s", e)

    def _click_publish_and_confirm(self, page):
        """点击发布按钮，并处理可能出现的确认弹窗（字数不足等）
        
        Returns:
            (success: bool, error: Optional[str]) 
            success=True 表示按钮已点击且无即时错误
            success=False 表示未找到按钮或遇到配额限制等错误
        """
        # 先滚动到底部让发布按钮可见
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)
        save_screenshot(page, "sohu_publish_scroll")

        publish_clicked = False

        # Step 1: 【关键】JS 探测页面底部所有可见按钮，按文本精确点击"发布"
        try:
            js_result = page.evaluate("""() => {
                const viewportH = window.innerHeight;
                const viewportW = window.innerWidth;
                const allBtns = Array.from(document.querySelectorAll('button, a, li, div, span'));
                
                // 收集页面底部区域（viewport 下半部分）的所有可见按钮
                const candidates = [];
                for (const btn of allBtns) {
                    const style = window.getComputedStyle(btn);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                    
                    const rect = btn.getBoundingClientRect();
                    const text = (btn.innerText || btn.textContent || '').trim();
                    if (!text || text.length > 20) continue; // 过滤大文本块
                    
                    // 只收集在 viewport 下半部分且可点击的元素
                    if (rect.top > viewportH * 0.6 && rect.width > 30 && rect.height > 20) {
                        candidates.push({
                            text: text,
                            className: btn.className || '',
                            tag: btn.tagName,
                            x: Math.round(rect.left),
                            y: Math.round(rect.top),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                            isPrimary: style.backgroundColor.includes('255, 36') || 
                                       style.backgroundColor.includes('255, 215') ||
                                       (btn.className || '').includes('primary') ||
                                       (btn.className || '').includes('positive')
                        });
                    }
                }
                
                // 找文本为"发布"的按钮，优先点击 primary/黄色样式的
                const publishBtns = candidates.filter(c => c.text === '发布');
                if (publishBtns.length > 0) {
                    // 优先点击 primary 样式的
                    const primary = publishBtns.find(b => b.isPrimary);
                    const target = primary || publishBtns[0];
                    
                    // 通过 elementFromPoint 获取真实元素并点击
                    const el = document.elementFromPoint(target.x + target.w/2, target.y + target.h/2);
                    if (el) {
                        el.click();
                        return {clicked: true, target: target, method: 'js_bottom_text', candidates: candidates};
                    }
                }
                
                return {clicked: false, candidates: candidates};
            }""")
            logger.info("发布按钮探测结果: %s", js_result)
            if isinstance(js_result, dict) and js_result.get("clicked"):
                publish_clicked = True
                logger.info("发布按钮已点击 (JS精确文本: %s)", js_result.get("target"))
                page.wait_for_timeout(1500)
                save_screenshot(page, "sohu_publish_clicked_js")
        except Exception as e:
            logger.debug("JS点击发布按钮失败: %s", e)

        # Step 2: fallback — Playwright 全局文本定位"发布"
        if not publish_clicked:
            try:
                # 找页面底部可见的"发布"按钮
                publish_btn = page.locator("text=发布").filter({"visible": True}).first
                if publish_btn.count() > 0:
                    publish_btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    publish_btn.click()
                    publish_clicked = True
                    logger.info("发布按钮已点击 (Playwright文本定位)")
                    page.wait_for_timeout(1500)
                    save_screenshot(page, "sohu_publish_clicked_loc")
            except Exception as e:
                logger.debug("Playwright文本定位发布按钮失败: %s", e)

        # Step 3: fallback — 选择器列表
        if not publish_clicked:
            for selector in PUBLISH_BTN_SELECTORS:
                try:
                    btn = page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        btn.click()
                        logger.info("发布按钮已点击 (选择器: %s)", selector)
                        publish_clicked = True
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_click_publish_and_confirm）', exc_info=True)
                    continue

        if not publish_clicked:
            logger.warning("未找到发布按钮")
            return False, "未找到或未能点击发布按钮"

        # Step 2: 等待并检测错误提示或确认弹窗
        page.wait_for_timeout(2000)
        
        # 检测发布配额已满等错误提示
        try:
            quota_error = page.evaluate("""() => {
                const bodyText = document.body.innerText || '';
                if (bodyText.includes('已达上限') || bodyText.includes('今日发布')) {
                    // 找具体的提示元素
                    const selectors = ['.el-message', '.toast', '[class*="message"]', '[class*="tip"]', '[class*="alert"]'];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            const t = (el.innerText || '').trim();
                            if (t && (t.includes('上限') || t.includes('今日') || t.includes('配额'))) return t;
                        }
                    }
                    // fallback: 从 body 文本中提取
                    const m = bodyText.match(/您今日[^。!！\n]{0,80}/);
                    if (m) return m[0];
                    return '今日发布配额已满';
                }
                return null;
            }""")
            if isinstance(quota_error, str) and quota_error:
                logger.warning("搜狐号发布受限: %s", quota_error)
                save_screenshot(page, "sohu_quota_error")
                return False, quota_error
        except Exception:
            logger.debug('可选步骤失败，继续执行（_click_publish_and_confirm）', exc_info=True)
            pass
        
        # 先关闭可能的封面上传失败弹窗等干扰弹窗
        self._dismiss_toast_and_dialog(page)
        # 再处理字数不足确认弹窗
        confirm_result = page.evaluate("""() => {
            // 找包含"确认发布文章"或"不足200字"文本的弹窗
            const allDialogs = document.querySelectorAll('.el-message-box, .el-dialog, [role="dialog"], [class*="modal"], [class*="dialog"]');
            for (const dlg of allDialogs) {
                const dlgText = (dlg.innerText || dlg.textContent || '').trim();
                if (dlgText.includes('确认发布') || dlgText.includes('不足200字') || dlgText.includes('直接发布')) {
                    const btns = dlg.querySelectorAll('button, .el-button');
                    for (const btn of btns) {
                        const t = (btn.innerText || btn.textContent || '').trim();
                        if (t === '确定' || t === '确认') {
                            btn.click();
                            return {clicked: true, text: t, reason: 'publish_confirm'};
                        }
                    }
                }
            }
            // fallback: 全局搜索可见的确定按钮（在遮罩层内）
            const allBtns = Array.from(document.querySelectorAll('button'));
            const confirm = allBtns.find(b => {
                const t = (b.innerText || b.textContent || '').trim();
                return (t === '确定' || t === '确认') && window.getComputedStyle(b).display !== 'none';
            });
            if (confirm) {
                confirm.click();
                return {clicked: true, text: confirm.innerText || confirm.textContent, reason: 'fallback'};
            }
            return {clicked: false};
        }""")
        if isinstance(confirm_result, dict) and confirm_result.get("clicked"):
            logger.info("已点击确认弹窗: %s", confirm_result)
            page.wait_for_timeout(1500)
        else:
            logger.debug("未检测到确认弹窗")
        return True, None
