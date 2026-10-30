"""百家号 publisher — Playwright 浏览器自动化方案

参考 MediaCrawler 思路：
- Playwright + channel="chrome" 使用系统 Chrome
- headless=False 绕过反爬检测
- Cookie 注入登录态
- 从首页导航进入编辑器（直接访问编辑器 URL 会 404）

2026-04-26 更新：
- 适配百家号新版编辑器（contenteditable 标题 + iframe UEditor 正文）
- 增加弹窗关闭逻辑（AI引导、风险检测等）
- 增加编辑器加载完成检测
"""
from __future__ import annotations
import logging
import os
import traceback
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import parse_cookie_string, launch_browser_context, save_screenshot, wait_for_js_condition
from tools import storage

logger = logging.getLogger(__name__)

_HOME_URL = "https://baijiahao.baidu.com/"


def _md_to_html(content: str) -> str:
    try:
        import markdown as _md
        return _md.markdown(content, extensions=["tables", "fenced_code"])
    except ImportError:
        return f"<p>{content}</p>"


class BaijiahaoPublisher(BasePublisher):
    channel_name = "baijiahao"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    def process_body_images(self, content, image_paths, image_doi_map=None):
        """百家号：不预置占位符，图片由 _upload_body_images 动态插入编辑器。"""
        return content

    def _upload_body_images(self, page, cover_images: list[str], image_doi_map: dict | None = None) -> dict[str, str]:
        """在百家号 UEditor V2 中上传正文图片，返回 {原始路径: 上传后URL}。

        核心策略：利用 UE_V2 execCommand('insertimage') 直接插入 base64 data URI，
        百家号服务器会自动将 base64 转为 CDN URL（如 bjh/picproxy）。
        无需操作 toolbar、对话框或 file input。
        """
        import base64

        if not cover_images or len(cover_images) <= 1:
            return {}
        results: dict[str, str] = {}

        def _get_iframe_img_urls() -> set[str]:
            urls = page.evaluate("""() => {
                const iframe = document.getElementById('ueditor_0');
                if (!iframe) return [];
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                return Array.from(doc.querySelectorAll('img')).map(i => i.getAttribute('src') || i.src);
            }""") or []
            return {u for u in urls if isinstance(u, str) and u.startswith("http")}

        def _move_cursor_to_end():
            page.evaluate("""() => {
                try {
                    // 先 focus iframe body 确保编辑器有焦点
                    const iframe = document.getElementById('ueditor_0');
                    if (iframe) {
                        const doc = iframe.contentDocument || iframe.contentWindow.document;
                        if (doc && doc.body) doc.body.focus();
                    }
                    const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                    if (ue) {
                        ue.focus();
                        const range = ue.selection.getRange();
                        range.collapse(false);
                        range.select();
                    }
                } catch(e) {}
            }""")
            page.wait_for_timeout(500)

        for idx, path in enumerate(cover_images[1:], start=1):
            resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
            if not resolved or not resolved.exists():
                logger.warning("百家号 body image not found: %s", path)
                continue

            # 读取图片并转为 base64 data URI
            try:
                with open(resolved, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
                ext = resolved.suffix.lower().replace(".", "")
                if ext == "jpg":
                    ext = "jpeg"
                mime = f"image/{ext}" if ext in ("png", "jpeg", "jpg", "gif", "webp") else "image/png"
                data_uri = f"data:{mime};base64,{img_b64}"
            except Exception as e:
                logger.warning("百家号图片 base64 编码失败 %s: %s", path, e)
                continue

            def try_upload():
                before_urls = _get_iframe_img_urls()
                _move_cursor_to_end()

                # 使用 UE_V2 execCommand 插入 base64 图片
                # 百家号服务器会自动将 base64 转为 CDN URL
                # 注意：必须先 click iframe body 获取焦点，否则图片会插入在开头
                page.evaluate("""(dataUri) => {
                    try {
                        const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                        if (ue) {
                            // 确保编辑器有焦点，选区在末尾
                            ue.focus();
                            const range = ue.selection.getRange();
                            range.collapse(false);
                            range.select();
                            ue.execCommand('insertimage', {src: dataUri, width: '', height: ''});
                            return {ok: true};
                        }
                        return {ok: false, reason: 'no ue'};
                    } catch(e) {
                        return {ok: false, error: e.toString()};
                    }
                }""", data_uri)
                logger.info("百家号 base64 图片已插入，等待服务器转换...")
                page.wait_for_timeout(4000)  # 等待服务器转换 base64 → CDN URL

                # 提取新增图片 URL
                after_urls = _get_iframe_img_urls()
                new_urls = [u for u in after_urls if u not in before_urls and u.startswith("http")]
                if new_urls:
                    return new_urls[-1]
                return None

            url = try_upload()
            is_success = bool(url)

            if is_success:
                results[path] = url
                logger.info("百家号 body image uploaded: %s -> %s", path, url)
                doi = (image_doi_map or {}).get(path)
                if doi:
                    page.evaluate("""(doi) => {
                        try {
                            const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                            if (ue) {
                                ue.execCommand('insertHtml', '<p>（引自：' + doi + '）</p>');
                            }
                        } catch(e) {}
                    }""", doi)
                    page.wait_for_timeout(300)
            else:
                logger.warning("百家号 body image upload failed, retrying: %s", path)
                page.wait_for_timeout(3000)
                url2 = try_upload()
                if url2:
                    results[path] = url2
                    logger.info("百家号 body image uploaded on retry: %s -> %s", path, url2)
                    doi = (image_doi_map or {}).get(path)
                    if doi:
                        page.evaluate("""(doi) => {
                            try {
                                const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                                if (ue) {
                                    ue.execCommand('insertHtml', '<p>（引自：' + doi + '）</p>');
                                }
                            } catch(e) {}
                        }""", doi)
                        page.wait_for_timeout(300)
                else:
                    logger.warning("百家号 body image retry failed: %s", path)

            if idx < len(cover_images) - 1:
                page.wait_for_timeout(3000)

        return results

    def _replace_image_placeholders(self, page, url_map: dict[str, str]):
        """在 UEditor 中替换本地路径为上传后的 URL"""
        if not url_map:
            return
        for local_path, url in url_map.items():
            try:
                page.evaluate(r"""(localPath, remoteUrl) => {
                    // 策略1: UEditor V2 API
                    try {
                        const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                        if (ue && ue.setContent) {
                            let html = ue.getContent();
                            const escaped = localPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                            const regex = new RegExp('src=["\']' + escaped + '["\']', 'g');
                            const newHtml = html.replace(regex, 'src="' + remoteUrl + '"');
                            if (newHtml !== html) {
                                ue.setContent(newHtml);
                                if (ue.fireEvent) ue.fireEvent('contentChange');
                                return {ok: true, method: 'UE_V2'};
                            }
                        }
                    } catch(e) {}
                    // 策略2: iframe body innerHTML
                    const iframe = document.getElementById('ueditor_0');
                    if (iframe) {
                        const doc = iframe.contentDocument || iframe.contentWindow.document;
                        const body = doc.body;
                        if (body) {
                            const imgs = body.querySelectorAll('img');
                            for (const img of imgs) {
                                if (img.src && img.src.includes(localPath)) {
                                    img.src = remoteUrl;
                                    return {ok: true, method: 'iframe_img'};
                                }
                            }
                        }
                    }
                    return {ok: false};
                }""", local_path, url)
            except Exception as e:
                logger.debug("百家号 replace placeholder failed for %s: %s", local_path, e)

    def publish(self, draft_id: int, title: str, content: str,
                cover_images: Optional[List[str]] = None, **kwargs) -> PublishResult:
        cookie = self.cookie
        if not cookie:
            return PublishResult(
                success=False, channel=self.channel_name,
                error="Baijiahao cookie not configured"
            )

        image_doi_map = kwargs.get("image_doi_map") or {}
        # 追加剩余图片到正文（markdown 占位符，后续需实际上传替换 URL）
        content = self.process_body_images(content, cover_images or [], image_doi_map=image_doi_map)

        try:
            check_cancel = kwargs.get("check_cancel")
            return self._publish_with_playwright(title, content, cover_images, image_doi_map=image_doi_map, check_cancel=check_cancel)
        except Exception as e:
            logger.error("Baijiahao publish failed: %s", e)
            logger.error(traceback.format_exc())
            return PublishResult(success=False, channel=self.channel_name, error="发布失败，请稍后重试")

    def _publish_with_playwright(self, title: str, content: str,
                                  cover_images: Optional[List[str]] = None,
                                  image_doi_map: dict | None = None,
                                  check_cancel=None) -> PublishResult:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context = launch_browser_context(p)
            context.add_cookies(parse_cookie_string(self.cookie, ".baidu.com"))
            page = context.new_page()
            try:

                # 1) 直接访问图文编辑器（跳过首页导航）
                editor_url = "https://baijiahao.baidu.com/builder/rc/edit?type=news&is_from_cms=1"
                page.goto(editor_url, wait_until="domcontentloaded", timeout=120000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
                page.wait_for_timeout(5000)

                if "login" in page.url or "passport" in page.url:
                    save_screenshot(page, "bjh_login_redirect")
                    browser.close()
                    return PublishResult(
                        success=False, channel=self.channel_name,
                        error="Baijiahao cookie expired"
                    )

                current_url = page.url
                logger.info("Baijiahao editor URL: %s", current_url)
                save_screenshot(page, "bjh_editor_loaded")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 3) 等待编辑器完全加载
                editor_ready = self._wait_for_editor(page)
                if not editor_ready:
                    save_screenshot(page, "bjh_editor_not_ready")
                    logger.warning("Editor may not be fully loaded, continuing anyway")

                # 4) 填入标题（先填标题，避免弹窗关闭改变焦点）
                self._fill_title(page, title)

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 5) 填入内容（将正文图片 base64 直接嵌入 HTML，setContent 一次性设置）
                html_content = _md_to_html(content)
                # 如果有 body images，将 base64 data URI 嵌入 HTML 末尾
                if cover_images and len(cover_images) > 1:
                    import base64
                    image_blocks: list[str] = []
                    for path in cover_images[1:]:
                        resolved = storage.resolve_storage_path(path, os.environ.get("WORKSPACE", "."))
                        if not resolved or not resolved.exists():
                            logger.warning("百家号 body image not found: %s", path)
                            continue
                        try:
                            with open(resolved, "rb") as f:
                                img_b64 = base64.b64encode(f.read()).decode("utf-8")
                            ext = resolved.suffix.lower().replace(".", "")
                            if ext == "jpg":
                                ext = "jpeg"
                            mime = f"image/{ext}" if ext in ("png", "jpeg", "jpg", "gif", "webp") else "image/png"
                            data_uri = f"data:{mime};base64,{img_b64}"
                            image_blocks.append(f'<p><img src="{data_uri}"></p>')
                            doi = (image_doi_map or {}).get(path)
                            if doi:
                                image_blocks.append(f'<p>（引自：{doi}）</p>')
                        except Exception as e:
                            logger.warning("百家号图片 base64 编码失败 %s: %s", path, e)
                    if image_blocks:
                        html_content = html_content + "\n" + "\n".join(image_blocks)
                        logger.info("百家号 HTML 中已嵌入 %d 张 body image (base64)", len(image_blocks) // 2)
                self._fill_content(page, html_content)
                save_screenshot(page, "bjh_content_filled")
                # 等待 UEditor 自动上传 base64 图片到服务器
                if cover_images and len(cover_images) > 1:
                    page.wait_for_timeout(5000)
                    save_screenshot(page, "bjh_body_images_uploaded")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 6) 上传封面
                if cover_images:
                    self._upload_cover(page, cover_images[0])
                    save_screenshot(page, "bjh_cover_uploaded")

                # 7) 关闭各类弹窗（AI引导、风险检测等）
                self._close_modals(page)
                page.wait_for_timeout(1000)
                self._close_modals(page)
                save_screenshot(page, "bjh_before_publish")

                # 9) 点击发布
                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                publish_ok = self._click_publish(page)
                if not publish_ok:
                    # 备选：保存草稿，证明流程跑通
                    save_draft_ok = self._click_save_draft(page)
                    save_screenshot(page, "bjh_publish_and_save_failed")
                    browser.close()
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error="未找到发布按钮" + ("，已保存草稿" if save_draft_ok else ""),
                    )

                # 严格检测发布是否真正成功
                # 百家号发布是 AJAX，页面通常不跳转，需要检测 toast/notification 消息
                # 先尝试条件等待 toast 出现（最多 6s），再兜底等待
                toast_appeared = wait_for_js_condition(page, """() => {
                    const selectors = ['.ant-message', '.ant-notification', '[class*="toast"]', '[class*="notification"]', '[class*="msg"]', '[class*="hint"]'];
                    for (const s of selectors) {
                        const el = document.querySelector(s);
                        if (el && el.textContent.trim()) return true;
                    }
                    return false;
                }""", timeout_ms=6000, poll_ms=300)
                if not toast_appeared:
                    page.wait_for_timeout(3000)
                current_url = page.url
                save_screenshot(page, "bjh_after_publish")

                # 读取页面文本，检测审核/成功/失败状态
                page_text = ""
                try:
                    raw_text = page.evaluate("""() => document.body ? (document.body.innerText || '') : ''""")
                    page_text = raw_text if isinstance(raw_text, str) else ""
                except Exception as e:
                    logger.warning("Failed to read page text: %s", e)

                # 读取 toast/notification 消息（比 body.innerText 更精确）
                toast_text = ""
                try:
                    raw_toast = page.evaluate("""() => {
                        const selectors = ['.ant-message', '.ant-notification', '[class*="toast"]', '[class*="notification"]', '[class*="msg"]', '[class*="hint"]'];
                        for (const s of selectors) {
                            const el = document.querySelector(s);
                            if (el && el.textContent.trim()) return el.textContent.trim();
                        }
                        return '';
                    }""")
                    toast_text = raw_toast if isinstance(raw_toast, str) else ""
                except Exception as e:
                    logger.warning("Failed to read toast text: %s", e)

                audit_keywords = ["审核中", "等待审核", "提交成功", "已提交审核", "发布成功", "内容已提交", "进入审核", "审核通过"]
                error_keywords = ["发布失败", "审核未通过", "内容违规", "请修改后重试", "包含违规内容", "请添加封面", "添加封面", "标题不能为空", "请填写标题"]
                success_toast_keywords = ["发布成功", "提交成功", "已提交审核", "审核中"]

                has_audit_hint = any(kw in page_text for kw in audit_keywords)
                has_error_hint = any(kw in page_text for kw in error_keywords)
                has_success_toast = any(kw in toast_text for kw in success_toast_keywords)

                # 百家号有平台审核机制：
                # 1. URL 离开编辑器页面 → 成功
                # 2. 页面文本中有审核/成功提示 → 成功
                # 3. toast 中有成功提示 → 成功（即使 URL 未变化）
                is_success = ("edit" not in current_url and "editor" not in current_url) or has_audit_hint or has_success_toast

                page_msg = toast_text
                if not is_success and not has_error_hint:
                    logger.warning("Baijiahao publish did not navigate away from editor. URL: %s", current_url)
                    if toast_text:
                        logger.warning("Baijiahao toast message: %s", toast_text)

                logger.info("Baijiahao success check: is_success=%s, has_audit_hint=%s, has_success_toast=%s, has_error_hint=%s, url=%s",
                            is_success, has_audit_hint, has_success_toast, has_error_hint, current_url)

                browser.close()

                if has_error_hint:
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error=f"百家号平台返回错误：{page_text[:200]}...",
                        raw={"current_url": current_url, "page_text": page_text[:500], "toast_text": toast_text},
                    )

                if not is_success:
                    return PublishResult(
                        success=False,
                        channel=self.channel_name,
                        error=f"发布未成功：页面仍停留在编辑器。URL: {current_url}",
                        raw={"current_url": current_url, "page_msg": page_msg, "page_text": page_text[:500]},
                    )

                return PublishResult(
                    success=True,
                    channel=self.channel_name,
                    url=current_url if "baijiahao.baidu.com" in current_url else None,
                    raw={"current_url": current_url, "page_text": page_text[:500], "toast_text": toast_text},
                )

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
    def _click_publish_works(self, page):
        """点击首页的'发布作品'按钮（直接 JS click，避免 locator 超时）"""
        try:
            page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('button, a, div, span'));
                const btn = all.find(el => el.textContent.trim() === '发布作品');
                if (btn) btn.click();
            }""")
            logger.info("Clicked '发布作品' via JS")
        except Exception as e:
            if "Execution context was destroyed" in str(e):
                logger.info("Clicked '发布作品' via JS (navigation expected)")
            else:
                logger.warning("Click '发布作品' failed: %s", e)

    def _select_article_type(self, page):
        """从下拉菜单选择'图文'"""
        try:
            loc = page.locator("text=图文").first
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                logger.info("Selected '图文' via locator")
                return
        except Exception:
            logger.debug('可选步骤失败，继续执行（_select_article_type）', exc_info=True)
            pass
        try:
            page.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('a, div, button, span'));
                const opt = all.find(el => el.textContent.trim() === '图文');
                if (opt) opt.click();
            }""")
            logger.info("Selected '图文' via JS")
        except Exception as e:
            if "Execution context was destroyed" in str(e):
                logger.info("Selected '图文' via JS (navigation expected)")
            else:
                logger.warning("Select '图文' failed: %s", e)
        logger.info("Selected '图文'")

    def _close_modals(self, page):
        """关闭各类弹窗和遮罩（只点击按钮，不删除 DOM）"""
        for _ in range(3):
            try:
                page.evaluate("""() => {
                    // 1) 点击常见关闭/确认按钮
                    const texts = ['我知道了', '下一步', '关闭', '取消', '暂不', '跳过', '确定', '确认', '完成', '使用AI'];
                    const btns = Array.from(document.querySelectorAll('button, span, div, a'));
                    for (const t of texts) {
                        for (const b of btns) {
                            if (b.textContent.trim() === t) { b.click(); }
                        }
                    }
                    // 2) 点击所有 X 关闭按钮
                    document.querySelectorAll('.cheetah-modal-close, [class*="close"], .ant-modal-close, .el-message-box__close, .dialog-close, .icon-close, [aria-label="close"]').forEach(el => el.click());
                    // 3) 点击遮罩层关闭 modal（如果 modal 支持点击遮罩关闭）
                    document.querySelectorAll('.ant-modal-wrap, .cheetah-modal-mask, [class*="modal-mask"], [class*="overlay"]').forEach(el => {
                        if (el.style.display !== 'none') el.click();
                    });
                }""")
                page.wait_for_timeout(600)
            except Exception:
                logger.debug('可选步骤失败，继续执行（_close_modals）', exc_info=True)
                pass
        logger.info("Modals closed")

    def _wait_for_editor(self, page) -> bool:
        """等待编辑器加载完成（至少有一个 contenteditable 可见，最长 15 秒）"""
        try:
            page.wait_for_function("""() => {
                const editables = document.querySelectorAll('[contenteditable="true"]');
                for (const el of editables) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 20) return true;
                }
                return false;
            }""", timeout=15000)
            logger.info("Editor ready")
            return True
        except Exception:
            logger.warning("Editor not ready within 15s")
            return False

    def _fill_title(self, page, title: str):
        title = title[:64]  # 百家号标题限制 64 字
        # 百家号使用 Lexical 编辑器，直接 innerText = ... 不会被编辑器识别。
        # 必须使用真实键盘输入（focus + click + keyboard.type），触发完整的 beforeinput/input 事件链。
        try:
            # 1) 先通过 JS 精确定位并 focus
            found = page.evaluate("""() => {
                const container = document.querySelector('[data-testid="news-title-input"]');
                let el = null;
                if (container) {
                    el = container.querySelector('[contenteditable="true"]');
                }
                if (!el) {
                    const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                    for (const e of editables) {
                        const rect = e.getBoundingClientRect();
                        if (rect.top < 250 && rect.width > 400 && rect.height < 80) {
                            el = e; break;
                        }
                    }
                }
                if (!el && document.querySelectorAll('[contenteditable="true"]').length > 0) {
                    el = document.querySelectorAll('[contenteditable="true"]')[0];
                }
                if (el) {
                    el.scrollIntoView({behavior: 'instant', block: 'center'});
                    el.focus();
                    el.click();
                    // 全选已有内容（如果有）
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                    return { found: true };
                }
                return { found: false };
            }""")
            if not (found and isinstance(found, dict) and found.get("found")):
                raise RuntimeError("title element not found")

            page.wait_for_timeout(200)
            # 2) 用 Playwright keyboard 真实输入（Lexical 能正确识别）
            page.keyboard.type(title)
            logger.info("Baijiahao title typed via keyboard")
            page.wait_for_timeout(500)

            # 3) 验证标题是否真的写入了
            verify = page.evaluate("""() => {
                const container = document.querySelector('[data-testid="news-title-input"]');
                let el = container ? container.querySelector('[contenteditable="true"]') : null;
                if (!el) {
                    const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                    for (const e of editables) {
                        const rect = e.getBoundingClientRect();
                        if (rect.top < 250 && rect.width > 400 && rect.height < 80) {
                            el = e; break;
                        }
                    }
                }
                if (!el) return { ok: false, reason: 'no_el' };
                const text = (el.innerText || el.textContent || '').trim();
                return { ok: text.length > 0, text: text.slice(0, 20) };
            }""")
            if verify and isinstance(verify, dict) and not verify.get("ok"):
                logger.warning("Baijiahao title verification failed, retrying with execCommand")
                # fallback: 用 execCommand insertText
                page.evaluate("""(title) => {
                    const container = document.querySelector('[data-testid="news-title-input"]');
                    let el = container ? container.querySelector('[contenteditable="true"]') : null;
                    if (!el) {
                        const editables = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                        for (const e of editables) {
                            const rect = e.getBoundingClientRect();
                            if (rect.top < 250 && rect.width > 400 && rect.height < 80) {
                                el = e; break;
                            }
                        }
                    }
                    if (el) {
                        el.focus();
                        el.click();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                        document.execCommand('insertText', false, title);
                    }
                }""", title)
                page.wait_for_timeout(500)
            else:
                logger.info("Baijiahao title verified: %s", verify.get("text") if isinstance(verify, dict) else "")
        except Exception as e:
            logger.warning("Baijiahao title fill failed: %s", e)

    def _fill_content(self, page, content: str):
        """填入正文（百家号新版编辑器）

        核心机制：百家号使用 window.editor 对象管理内容，直接修改 innerHTML
        不会被字数统计识别。必须使用 window.editor.setContent() 并触发
        contentChange 事件。
        """
        # 策略1: 使用 UEditor V2 API（百家号新版编辑器）
        js_result = page.evaluate("""(content) => {
            // 尝试 UEditor V2 API
            try {
                const ue = window.UE_V2 && window.UE_V2.instants && window.UE_V2.instants['ueditorInstant0'];
                if (ue && ue.setContent) {
                    ue.setContent(content);
                    if (ue.fireEvent) ue.fireEvent('contentChange');
                    return {success: true, method: 'UE_V2'};
                }
            } catch(e) {}
            // 尝试旧版 window.editor
            if (window.editor && window.editor.setContent) {
                try {
                    window.editor.setContent(content);
                    if (window.editor.fireEvent) {
                        window.editor.fireEvent('contentChange');
                    }
                    return {success: true, method: 'editor_setContent'};
                } catch(e) {
                    return {error: e.toString()};
                }
            }
            return {error: 'UE/editor not available'};
        }""", content)
        if js_result and isinstance(js_result, dict) and js_result.get("success"):
            logger.info("Baijiahao content filled via %s", js_result.get("method"))
            page.wait_for_timeout(1500)
            return
        if js_result and isinstance(js_result, dict) and js_result.get("error"):
            logger.debug("editor.setContent failed: %s", js_result["error"])

        # 策略2: 通过 ueditor_0 iframe 直接填入（fallback）
        js_result2 = page.evaluate("""(content) => {
            const iframe = document.getElementById('ueditor_0');
            if (iframe) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    const body = doc.body;
                    if (body) {
                        body.innerHTML = content;
                        body.dispatchEvent(new Event('input', { bubbles: true }));
                        return 'ueditor_0';
                    }
                } catch(e) {}
            }
            // fallback: 遍历所有 iframe
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    const body = doc.body;
                    if (body && body.innerHTML !== undefined) {
                        body.innerHTML = content;
                        body.dispatchEvent(new Event('input', { bubbles: true }));
                        return 'iframe';
                    }
                } catch(e) {}
            }
            return false;
        }""", content)
        if js_result2:
            logger.info("Baijiahao content filled via %s (fallback)", js_result2)
            page.wait_for_timeout(1500)
            return

        # 策略3: 通过 contenteditable 键盘输入（最后 fallback）
        try:
            iframe_els = page.query_selector_all('iframe#ueditor_0')
            if iframe_els:
                iframe = iframe_els[0].content_frame()
                iframe.locator('body').type(content)
                logger.info("Baijiahao content typed via iframe keyboard")
                page.wait_for_timeout(1500)
                return
        except Exception as e:
            logger.debug("iframe keyboard type failed: %s", e)

        logger.warning("Baijiahao content editor not found")

    def _upload_cover(self, page, img_path: str):
        """上传封面（百家号新版编辑器）

        核心机制：
        1. 优先直接查找页面中的 file input 并设置文件（React 组件可能已预渲染隐藏 input）
        2. 如果直接设置失败，点击封面区域打开上传对话框后再设置
        注意：百家号要求封面最小尺寸 372×248，过小会被 beforeUpload 拒绝。
        """
        if not img_path:
            return
        p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
        if p is None or not p.exists():
            logger.warning("Cover image not found: %s", img_path)
            return

        # 尺寸预检（百家号 beforeUpload 要求 >= 372×248）
        try:
            from PIL import Image as PILImage
            with PILImage.open(p) as img:
                w, h = img.size
                if w < 372 or h < 248:
                    logger.warning(
                        "Baijiahao cover image too small (%dx%d < 372x248), upload will be rejected: %s",
                        w, h, img_path,
                    )
                    return
        except Exception:
            logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
            pass  # 如果 PIL 不可用或读取失败，继续尝试上传

        try:
            self._close_modals(page)
            page.wait_for_timeout(800)

            file_set = False

            # 策略0: 只在封面区域内查找 file input（避免误触视频上传等其他 input）
            try:
                # 先定位封面区域容器，再在其内部找 input[type="file"]
                cover_area = page.locator('[class*="cover"], [class*=" Cover"], [data-testid*="cover"]').first
                if cover_area.count() > 0:
                    file_inputs = cover_area.locator('input[type="file"]').all()
                    for fi in file_inputs:
                        try:
                            fi.set_input_files(str(p.resolve()))
                            logger.info("Baijiahao cover file set via cover-area input[type=file]")
                            file_set = True
                            break
                        except Exception:
                            logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                            continue
            except Exception as e:
                logger.debug("Cover-area file input strategy failed: %s", e)

            # 策略1: 点击封面区域打开上传面板，再通过 filechooser 或限定在面板内上传
            if not file_set:
                dialog_opened = False
                click_strategies = [
                    (lambda: page.locator("text=选择封面").first.click(force=True, timeout=5000), "text=选择封面"),
                    (lambda: page.get_by_text("选择封面", exact=False).first.click(force=True, timeout=5000), "get_by_text"),
                    (lambda: page.evaluate("""() => {
                        const els = Array.from(document.querySelectorAll('span, div, button'));
                        const el = els.find(e => e.textContent.trim() === '选择封面');
                        if (el) { el.click(); return true; }
                        return false;
                    }"""), "js_click"),
                ]
                for click_fn, name in click_strategies:
                    try:
                        click_fn()
                        logger.info("Baijiahao cover area clicked (strategy=%s)", name)
                        dialog_opened = True
                        break
                    except Exception as e:
                        logger.debug("Cover click strategy %s failed: %s", name, e)

                if dialog_opened:
                    page.wait_for_timeout(2000)

                    # 1a. 在弹出的面板/对话框里点击「本地上传」等文字，触发 filechooser 上传
                    upload_triggered = False
                    for trigger_text in ["点击本地上传", "本地上传", "上传封面", "本地图片"]:
                        try:
                            loc = page.get_by_text(trigger_text, exact=False).first
                            if loc.count() > 0 and loc.is_visible():
                                with page.expect_event("filechooser", timeout=10000) as fc_info:
                                    loc.click(force=True)
                                fc = fc_info.value
                                fc.set_files(str(p.resolve()))
                                logger.info("Baijiahao cover uploaded via filechooser (trigger=%s)", trigger_text)
                                file_set = True
                                upload_triggered = True
                                break
                        except Exception as e:
                            logger.debug("Filechooser trigger '%s' failed: %s", trigger_text, e)

                    # 1b. 兜底：只在对话框/模态框/面板内部查找 input[type="file"]
                    if not upload_triggered:
                        try:
                            modal_selectors = [
                                '.ant-modal',
                                '[class*="modal"]',
                                '[class*="dialog"]',
                                '[role="dialog"]',
                                '[class*="upload-dialog"]',
                                '[class*="cover-dialog"]',
                                '[class*="popover"]',
                                '[class*="dropdown"]',
                            ]
                            for modal_sel in modal_selectors:
                                modal = page.locator(modal_sel).first
                                if modal.count() > 0:
                                    inputs = modal.locator('input[type="file"]').all()
                                    for fi in inputs:
                                        try:
                                            fi.set_input_files(str(p.resolve()))
                                            logger.info("Baijiahao cover file set inside modal (%s)", modal_sel)
                                            file_set = True
                                            break
                                        except Exception:
                                            logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                                            continue
                                    if file_set:
                                        break
                        except Exception as e:
                            logger.debug("Modal file input strategy failed: %s", e)
                else:
                    logger.warning("Baijiahao cover upload: could not open cover dialog")

            # 策略2: 如果上述都失败，尝试直接找「点击本地上传」所在的可视区域用 filechooser
            if not file_set:
                try:
                    upload_area = page.locator("text=点击本地上传").first
                    if upload_area.count() > 0 and upload_area.is_visible():
                        with page.expect_event("filechooser", timeout=10000) as fc_info:
                            upload_area.click(force=True)
                        fc = fc_info.value
                        fc.set_files(str(p.resolve()))
                        logger.info("Baijiahao cover file set via direct filechooser")
                        file_set = True
                except Exception as e:
                    logger.debug("Direct filechooser strategy failed: %s", e)

            # 策略3: base64 DataTransfer fallback（仅操作最可能是封面的 input）
            if not file_set:
                try:
                    import base64
                    with open(p, "rb") as f:
                        file_b64 = base64.b64encode(f.read()).decode("utf-8")
                    result = page.evaluate("""({b64, name, type}) => {
                        try {
                            const byteChars = atob(b64);
                            const byteNumbers = new Array(byteChars.length);
                            for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i);
                            const byteArray = new Uint8Array(byteNumbers);
                            const blob = new Blob([byteArray], {type: type});
                            const file = new File([blob], name, {type: type});
                            // 优先找封面区域内的 input（通过父元素 class 判断）
                            const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
                            // 按 DOM 位置排序：优先选择在 .cover / [class*="cover"] 容器内的 input
                            let target = null;
                            for (const inp of allInputs) {
                                let parent = inp.parentElement;
                                for (let depth = 0; depth < 6 && parent; depth++, parent = parent.parentElement) {
                                    const cls = parent.className || '';
                                    if (cls.toLowerCase().includes('cover') || cls.toLowerCase().includes('upload')) {
                                        target = inp;
                                        break;
                                    }
                                }
                                if (target) break;
                            }
                            // 如果找不到封面相关容器，跳过（避免误触视频上传框）
                            if (!target) return {error: 'no cover-area input found'};
                            const dt = new DataTransfer();
                            dt.items.add(file);
                            target.files = dt.files;
                            target.dispatchEvent(new Event('change', {bubbles: true}));
                            target.dispatchEvent(new Event('input', {bubbles: true}));
                            return {success: true, filesLength: target.files.length};
                        } catch (e) {
                            return {error: e.toString()};
                        }
                    }""", {"b64": file_b64, "name": p.name, "type": "image/png"})
                    if result and isinstance(result, dict) and result.get("success"):
                        logger.info("Baijiahao cover uploaded via base64 DataTransfer")
                        file_set = True
                    else:
                        logger.debug("Base64 upload result: %s", result)
                except Exception as e:
                    logger.debug("Cover base64 strategy failed: %s", e)

            if file_set:
                # 上传需要一定时间（beforeUpload 含尺寸校验 + 图片上传）
                page.wait_for_timeout(5000)
                save_screenshot(page, "bjh_cover_after_upload")
                # 点击"确定"关闭对话框
                confirm_clicked = False
                for text in ["确定", "确认", "完成"]:
                    try:
                        btn = page.locator(f'button:has-text("{text}")').first
                        if btn.count() > 0:
                            btn.click(force=True)
                            logger.info("Baijiahao cover confirm clicked: %s", text)
                            confirm_clicked = True
                            page.wait_for_timeout(2000)
                            break
                    except Exception:
                        logger.debug('可选步骤失败，继续执行（_upload_cover）', exc_info=True)
                        pass
                # fallback: JS 点击包含"确定"的按钮
                if not confirm_clicked:
                    page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button, span[role="button"]'));
                        for (const b of btns) {
                            if (b.textContent.includes('确定') || b.textContent.includes('确认')) {
                                b.click(); break;
                            }
                        }
                    }""")
                    page.wait_for_timeout(1000)
            else:
                logger.warning("Baijiahao cover upload: all strategies failed")

            self._close_modals(page)
        except Exception as e:
            logger.warning("Baijiahao cover upload failed: %s", e)

    def _click_publish(self, page) -> bool:
        """点击发布按钮，返回是否成功点击（不保证发布成功）"""
        # 先关闭所有弹窗，确保按钮不被遮挡
        self._close_modals(page)
        page.wait_for_timeout(1000)
        save_screenshot(page, "bjh_before_click_publish")

        # 策略1: Playwright locator 定位可见的"发布"按钮（force=True 绕过 SVG 遮罩）
        strategies = [
            ('button:has-text("发布"):not(:has-text("定时"))', 'text_filter'),
            ('button[class*="publish"]', 'class_publish'),
            ('button[class*="submit"]', 'class_submit'),
            ('[data-testid="publish-btn"]', 'testid'),
        ]
        for selector, name in strategies:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    loc.scroll_into_view_if_needed()
                    page.wait_for_timeout(300)
                    loc.click(force=True)
                    logger.info("Baijiahao publish button clicked (strategy=%s, selector=%s)", name, selector)
                    page.wait_for_timeout(3000)
                    self._handle_publish_dialog(page)
                    page.wait_for_timeout(1500)
                    save_screenshot(page, "bjh_after_click_publish")
                    return True
            except Exception as e:
                logger.debug("Strategy %s failed: %s", name, e)

        # 策略2: JS 遍历所有按钮，找可见的、包含"发布"文本的按钮
        # 使用完整的鼠标事件序列（mousedown + mouseup + click）模拟真实用户交互
        js_result = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"], a'));
            for (const b of btns) {
                const text = b.textContent || '';
                const rect = b.getBoundingClientRect();
                const style = window.getComputedStyle(b);
                const isVisible = rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                const isDisabled = b.disabled || style.pointerEvents === 'none' || b.getAttribute('aria-disabled') === 'true';
                if (isVisible && !isDisabled && text.includes('发布') && !text.includes('定时')) {
                    b.scrollIntoView({behavior: 'instant', block: 'center'});
                    // 模拟完整的鼠标事件序列
                    const mousedown = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });
                    const mouseup = new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window });
                    const click = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                    b.dispatchEvent(mousedown);
                    b.dispatchEvent(mouseup);
                    b.dispatchEvent(click);
                    // 同时触发原生 click
                    b.click();
                    return {found: true, text: text.trim().slice(0, 20), tag: b.tagName};
                }
            }
            return {found: false};
        }""")
        if js_result and isinstance(js_result, dict) and js_result.get("found"):
            logger.info("Baijiahao publish button clicked via JS: text=%s", js_result.get("text") if isinstance(js_result, dict) else "unknown")
            page.wait_for_timeout(3000)
            self._handle_publish_dialog(page)
            page.wait_for_timeout(1500)
            save_screenshot(page, "bjh_after_click_publish_js")
            return True

        logger.warning("Baijiahao publish button not found after all strategies")
        return False

    def _click_save_draft(self, page) -> bool:
        """备选：点击保存草稿按钮，用于验证流程跑通"""
        try:
            loc = page.locator('button:has-text("保存草稿")').first
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                logger.info("Baijiahao '保存草稿' clicked")
                page.wait_for_timeout(2000)
                save_screenshot(page, "bjh_after_save_draft")
                return True
        except Exception as e:
            logger.debug("Save draft button not found: %s", e)
        return False

    def _handle_publish_dialog(self, page):
        """处理发布后的弹窗（如字数不足提示、风险提示等）"""
        for text in ['保持图文发布', '确定', '确认', '我知道了', '继续发布', '同意并发布']:
            try:
                btn = page.locator(f'button:has-text("{text}")').first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    logger.info("Clicked '%s' on publish dialog", text)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                logger.debug('可选步骤失败，继续执行（_handle_publish_dialog）', exc_info=True)
                pass
        # JS fallback
        page.evaluate("""() => {
            const texts = ['保持图文发布','确定','确认','我知道了','继续发布','同意并发布'];
            const btns = Array.from(document.querySelectorAll('button'));
            for (const t of texts) {
                const b = btns.find(x => x.textContent.includes(t));
                if (b) { b.click(); return; }
            }
        }""")
        page.wait_for_timeout(1000)
