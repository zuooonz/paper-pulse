"""小红书 publisher — Playwright 浏览器自动化方案

参考 MediaCrawler 思路：
- Playwright + channel="chrome" 使用系统 Chrome
- headless=False 绕过反爬检测
- Cookie 注入登录态

注意：小红书是图文笔记平台，发布时必须包含至少一张图片。
发布流程：
1. 访问创作者中心发布页
2. 点击"上传图文"标签
3. 上传图片（编辑区域在图片上传后才出现）
4. 填入标题和内容
5. 点击发布
"""
from __future__ import annotations
import logging
import os
import traceback
from typing import Optional, List

from .base import BasePublisher, PublishResult
from ._browser_mixin import (
    parse_cookie_string, save_screenshot, save_page_html, _is_headless, launch_browser_context,
    check_cookie_keys, wait_for_js_condition,
)
from tools import storage

logger = logging.getLogger(__name__)

_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?from=menu&target=image"


_XHS_TITLE_MAX = 20   # 小红书标题硬限制约 20 字
_XHS_CONTENT_MAX = 1000  # 小红书正文硬限制 1000 字


def _get_sync_playwright():
    """Use Patchright when installed, with Playwright as a compatible fallback."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "小红书发布需要浏览器自动化依赖，请安装 paper-pulse[browser]"
            ) from exc
    return sync_playwright


def _md_to_plaintext(content: str) -> str:
    """Markdown → 纯文本（保留标题文字和段落结构，只去掉格式标记）"""
    from publisher._content_preprocessor import ContentPreprocessor
    return ContentPreprocessor("prosemirror").process(content)


def preprocess_for_xiaohongshu(content: str, title: str = "") -> str:
    """
    小红书内容预处理：
    1. 删除 "## 局限性与边界" 和 "## 方法亮点" 整个章节（标题+正文）
    2. 提取内容中第一个 ## 行作为正文首句（论文标题），并删除该行
    3. Markdown 转纯文本（去掉格式标记，保留章节标题文字）
    4. 正文结构：标题\n\n正文
    5. 控制字数在 1000 以内
    """
    if not content:
        return ""

    lines = content.split("\n")

    # 提取第一个 ## 行作为正文标题（论文标题）
    content_title = ""
    for i, line in enumerate(lines):
        if line.startswith("## "):
            content_title = line[3:].strip()
            lines.pop(i)
            break

    # 删除指定章节（局限性与边界、方法亮点）
    sections_to_remove = ["局限性与边界", "方法亮点"]
    filtered_lines = []
    skip = False

    for line in lines:
        if line.startswith("## "):
            section_title = line[3:].strip()
            skip = any(target in section_title for target in sections_to_remove)
            if skip:
                continue
        if not skip:
            filtered_lines.append(line)

    # Markdown 转纯文本
    filtered = "\n".join(filtered_lines)
    plain = _md_to_plaintext(filtered)

    # 正文首句为标题（优先使用传入的 title，否则用内容中提取的）
    header = title or content_title
    if header:
        plain = header + "\n\n" + plain

    # 控制字数（小红书硬限制 1000 字）
    if len(plain) > _XHS_CONTENT_MAX:
        truncated = plain[:_XHS_CONTENT_MAX]
        last_punct = max(
            truncated.rfind("。"),
            truncated.rfind("？"),
            truncated.rfind("！"),
            truncated.rfind("."),
        )
        if last_punct > _XHS_CONTENT_MAX * 0.8:
            plain = truncated[: last_punct + 1]
        else:
            plain = truncated

    return plain.strip()


class XiaohongshuPublisher(BasePublisher):
    channel_name = "xiaohongshu"

    def __init__(self, cookie: str, timeout: int = 30):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.cookie)

    def publish(self, draft_id: int, title: str, content: str,
                cover_images: Optional[List[str]] = None, **kwargs) -> PublishResult:
        cookie = self.cookie
        if not cookie:
            return PublishResult(
                success=False, channel=self.channel_name,
                error="Xiaohongshu cookie not configured"
            )

        # 小红书是图文平台，必须有图片
        if not cover_images:
            return PublishResult(
                success=False, channel=self.channel_name,
                error="小红书发布需要至少一张图片，请为草稿关联包含图片的论文"
            )

        try:
            check_cancel = kwargs.get("check_cancel")
            image_doi_map = kwargs.get("image_doi_map") or {}
            return self._publish_with_playwright(
                title or "", content or "", cover_images,
                check_cancel=check_cancel, image_doi_map=image_doi_map,
            )
        except Exception as e:
            logger.error("小红书发布失败: %s", e)
            logger.error(traceback.format_exc())
            return PublishResult(
                success=False, channel=self.channel_name, error="发布失败，请稍后重试"
            )

    def _publish_with_playwright(self, title: str, content: str,
                                  cover_images: List[str],
                                  check_cancel=None,
                                  image_doi_map: dict[str, str] | None = None) -> PublishResult:
        """发布流程参考 xhs_ai_publisher：
        首页 → 点"发布笔记" → 切图文tab → 上传图片 → 填标题 → 填内容 → 点发布 → 确认
        """
        with _get_sync_playwright()() as p:
            headless = _is_headless()
            logger.warning("Xiaohongshu browser launching (headless=%s)", headless)

            key_status = check_cookie_keys(self.cookie)
            logger.warning("Cookie key check: %s", key_status)

            browser, context = launch_browser_context(p, inject_stealth=True)
            cookies = parse_cookie_string(
                self.cookie, ".xiaohongshu.com",
                extra_domains=["creator.xiaohongshu.com", "www.xiaohongshu.com"]
            )
            context.add_cookies(cookies)
            logger.warning("Added %d cookies for xiaohongshu", len(cookies))

            page = context.new_page()
            try:
                console_errors: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

                # 1) 直连图文发布页（cookie 已验证可访问）
                logger.warning("Navigating to publish page...")
                page.goto(_PUBLISH_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                save_screenshot(page, "xhs_page_loaded")

                if "login" in page.url or "signin" in page.url:
                    browser.close()
                    return PublishResult(success=False, channel=self.channel_name,
                        error="小红书 Cookie 已失效，请重新绑定账号")

                # 2) 上传图片
                if not cover_images:
                    browser.close()
                    return PublishResult(success=False, channel=self.channel_name,
                        error="小红书发布需要至少一张图片")
                uploaded = self._upload_images(page, cover_images)
                if not uploaded:
                    browser.close()
                    return PublishResult(success=False, channel=self.channel_name,
                        error="图片上传失败")
                page.wait_for_timeout(2000)
                save_screenshot(page, "xhs_after_upload")

                # 3) 填标题
                self._fill_title(page, title)
                save_screenshot(page, "xhs_title_filled")

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 4) 填内容
                plain = preprocess_for_xiaohongshu(content, title=title)
                if image_doi_map and cover_images:
                    caption_lines = []
                    for idx, path in enumerate(cover_images[1:], start=1):
                        doi = image_doi_map.get(path)
                        if doi:
                            caption_lines.append(f"（图{idx} 引自：{doi}）")
                    if caption_lines:
                        plain = plain + "\n\n" + "\n".join(caption_lines)
                        if len(plain) > _XHS_CONTENT_MAX:
                            plain = plain[:_XHS_CONTENT_MAX]
                self._fill_content(page, plain)
                save_screenshot(page, "xhs_content_filled")
                page.wait_for_timeout(3000)

                cancelled = self._check_cancelled(check_cancel)
                if cancelled:
                    browser.close()
                    return cancelled

                # 7) 点击"发布"按钮
                logger.warning("Clicking publish button...")
                save_screenshot(page, "xhs_before_publish")
                save_page_html(page, "xhs_before_publish")
                publish_ok = self._click_publish(page)
                if not publish_ok:
                    browser.close()
                    return PublishResult(success=False, channel=self.channel_name,
                        error="未找到发布按钮")
                page.wait_for_timeout(3000)
                save_screenshot(page, "xhs_after_publish")

                # 8) 处理"确认发布"弹窗
                try:
                    for sel in ["div[role='dialog'] button:has-text('确认发布')",
                                "div[role='dialog'] button:has-text('确认')",
                                "div[role='dialog'] button:has-text('确定')",
                                "button:has-text('确认发布')"]:
                        confirm = page.locator(sel).last
                        if confirm.count() > 0 and confirm.is_visible():
                            confirm.click(timeout=5000)
                            logger.warning("Clicked confirm dialog: %s", sel)
                            page.wait_for_timeout(2000)
                            break
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass

                # 9) 等待发布结果
                is_success = False
                final_url = page.url
                for _ in range(20):
                    page.wait_for_timeout(500)
                    final_url = page.url
                    # dispatchEvent('publish') 成功后 URL 带 published=true
                    if "published=true" in final_url:
                        is_success = True
                        break
                    if "/publish/success" in final_url:
                        is_success = True
                        break
                    page_text = page.evaluate("() => (document.body?.innerText || '').slice(0, 300)")
                    if any(kw in page_text for kw in ["发布成功", "提交成功", "审核中", "已发布"]):
                        is_success = True
                        break
                    if "creator.xiaohongshu.com" in final_url and "publish" not in final_url:
                        is_success = True
                        break

                save_screenshot(page, "xhs_final")
                browser.close()

                if is_success:
                    return PublishResult(success=True, channel=self.channel_name, url=final_url)
                else:
                    return PublishResult(success=False, channel=self.channel_name,
                        error=f"发布未确认成功，URL: {final_url}",
                        raw={"url": final_url, "console_errors": console_errors[-5:]})

            finally:
                try:
                    browser.close()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_publish_with_playwright）', exc_info=True)
                    pass
    def _upload_images(self, page, image_paths: List[str]) -> bool:
        """上传图片到小红书，返回是否成功"""
        valid_paths = []
        for img_path in image_paths[:9]:  # 小红书最多9张图
            p = storage.resolve_storage_path(img_path, os.environ.get("WORKSPACE", "."))
            if p and p.exists():
                valid_paths.append(str(p.resolve()))

        if not valid_paths:
            logger.error("No valid images to upload (paths: %s)", image_paths)
            return False

        # 预处理所有 file input：移除 accept 限制，添加 multiple 属性
        page.evaluate("""() => {
            document.querySelectorAll('input[type="file"]').forEach(el => {
                el.removeAttribute('accept');
                el.setAttribute('multiple', '');
                el.style.display = 'block';
                el.style.visibility = 'visible';
                el.style.opacity = '1';
            });
        }""")

        # 尝试找到可用的 file input
        for selector, name in [
            ("input[type='file'][accept*='image']", 'image_input'),
            ("input[type='file'][multiple]", 'multiple_input'),
            ("input[type='file']", 'any_file_input'),
        ]:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0:
                    files_to_upload = valid_paths
                    try:
                        loc.set_input_files(files_to_upload)
                    except Exception:
                        # 如果不支持多文件，只传第一个
                        logger.debug("Multi-file upload failed, trying single file")
                        loc.set_input_files(valid_paths[0])
                    logger.warning("Uploaded images via %s (valid=%d, attempted=%d)", name, len(valid_paths), len(files_to_upload))
                    page.wait_for_timeout(2000)
                    return True
            except Exception as e:
                logger.debug("Upload strategy %s failed: %s", name, e)

        # 最终兜底：找到任意可见 input，只传第一张
        try:
            loc = page.locator("input[type='file']").first
            if loc.count() > 0:
                loc.evaluate("el => { el.removeAttribute('accept'); el.setAttribute('multiple', ''); }")
                loc.set_input_files(valid_paths[0])
                logger.warning("Uploaded 1 cover image via fallback")
                page.wait_for_timeout(2000)
                return True
        except Exception as e:
            logger.error("Final upload fallback failed: %s", e)

        logger.error("All image upload strategies failed")
        return False

    def _switch_to_image_text_mode(self, page) -> bool:
        """切换到"上传图文"模式。

        通过 .creator-tab CSS class 定位 tab（"上传图文"的 tab 没有 active class）。
        使用 Playwright locator 真实点击。
        """
        # 等待 tab 元素渲染
        wait_for_js_condition(page, """() => {
            return document.querySelectorAll('.creator-tab').length >= 2;
        }""", timeout_ms=10000, poll_ms=500)

        # 通过 CSS class 找到非 active 的 tab（即"上传图文"tab）
        try:
            # .creator-tab:not(.active) 就是图文 tab
            tab = page.locator('.creator-tab:not(.active)').first
            if tab.count() > 0 and tab.is_visible():
                tab.scroll_into_view_if_needed()
                page.wait_for_timeout(300)
                tab.click()
                logger.warning("Clicked image-text tab via .creator-tab:not(.active)")
                page.wait_for_timeout(2000)
                return True
        except Exception as e:
            logger.warning("Tab click via CSS class failed: %s", e)

        # 兜底：JS 点击非 active 的 creator-tab
        try:
            page.evaluate("""() => {
                const tabs = document.querySelectorAll('.creator-tab');
                for (const t of tabs) {
                    if (!t.classList.contains('active')) {
                        t.scrollIntoView({block: 'center'});
                        t.click();
                        return;
                    }
                }
            }""")
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning("JS tab fallback failed: %s", e)

        return True

    def _fill_title(self, page, title: str):
        title = (title or "")[:_XHS_TITLE_MAX]  # 小红书标题限制约20字
        try:
            # 策略1: Playwright fill 模拟真实键盘输入，触发 Vue 响应式更新
            loc = page.locator("input.d-text").first
            if loc.count() > 0:
                loc.click()
                page.wait_for_timeout(300)
                loc.fill(title)
                logger.warning("Xiaohongshu title filled (playwright fill)")
                page.wait_for_timeout(500)
                # 唤醒 Vue 响应式：模拟一次末尾空格+删除
                page.keyboard.press("End")
                page.wait_for_timeout(100)
                page.keyboard.press("Space")
                page.wait_for_timeout(100)
                page.keyboard.press("Backspace")
                page.wait_for_timeout(500)
                return
        except Exception as e:
            logger.debug("Playwright fill title failed: %s", e)

        # 策略2: JS fallback（直接操作 DOM，可能无法触发 Vue 状态更新）
        js_result = page.evaluate("""(title) => {
            const inputs = Array.from(document.querySelectorAll('input.d-text'));
            for (const el of inputs) {
                const ph = el.placeholder || '';
                if (ph.includes('标题') || ph.includes('赞') || el.getAttribute('maxlength') === '20') {
                    el.value = title;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    // 额外触发 focus/blur 帮助 Vue 识别
                    el.dispatchEvent(new Event('focus', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    return 'placeholder_match';
                }
            }
            const first = inputs.find(el => el.type === 'text');
            if (first) {
                first.value = title;
                first.dispatchEvent(new Event('input', { bubbles: true }));
                first.dispatchEvent(new Event('focus', { bubbles: true }));
                first.dispatchEvent(new Event('blur', { bubbles: true }));
                return 'first_text_input';
            }
            return false;
        }""", title)
        if js_result:
            logger.warning("Xiaohongshu title filled (%s)", js_result)
        else:
            logger.warning("Xiaohongshu title input not found")
        page.wait_for_timeout(1000)

    def _fill_content(self, page, content: str):
        """填充正文内容

        小红书使用 tiptap ProseMirror 编辑器（contenteditable div）。
        之前使用 JS 直接设置 innerHTML，但 Vue 不响应原生 DOM 事件，
        导致发布按钮点击无反应。现改为 Playwright fill() 优先，
        模拟真实键盘输入以触发 Vue 响应式更新。

        注意：content 已由 preprocess_for_xiaohongshu() 完成预处理
        （删除章节、去 Markdown、控字数 ≤1000），此处直接填入即可。
        """
        text = content or ""
        # 若内容极短（<20 字），补充引导语避免被判定为空内容
        if len(text) < 20:
            text = text + "\n\n（更多精彩内容详见原文链接）"

        # 策略1: Playwright type 逐字输入（ProseMirror/tiptap 只响应真实键盘事件）
        try:
            editor = page.locator("div.tiptap.ProseMirror").first
            if editor.count() > 0:
                editor.click()
                page.wait_for_timeout(300)
                # 先清空原有内容
                page.keyboard.press("Control+a")
                page.wait_for_timeout(200)
                # 使用 type 而不是 fill：fill() 直接改 DOM，ProseMirror 不识别；
                # type() 为每个字符生成真实的 keydown/beforeinput/input/keyup 事件链
                editor.type(text, delay=5)
                logger.warning("Xiaohongshu content filled (playwright type, %d chars)", len(text))
                page.wait_for_timeout(800)
                return
        except Exception as e:
            logger.debug("Playwright type content failed: %s", e)

        # 策略2: JS fallback（直接操作 DOM，可能无法触发 Vue 状态更新）
        try:
            filled = page.evaluate("""(text) => {
                try {
                    const eds = Array.from(document.querySelectorAll('div.tiptap.ProseMirror, [contenteditable="true"]'));
                    for (const el of eds) {
                        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') continue;
                        // ProseMirror/tiptap 需要 <p> 标签才能正确分段
                        // 使用字符串 split（避免正则，防止特殊字符导致 JS regex 解析错误）
                        const paragraphs = text.split('\\n\\n').filter(function(p) { return p.trim(); });
                        const html = paragraphs.map(function(p) {
                            const trimmed = p.trim();
                            // 章节标题行（以 > 开头）加粗显示
                            if (trimmed.startsWith('> ')) {
                                return '<p><strong>' + trimmed.slice(2).replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</strong></p>';
                            }
                            return '<p>' + trimmed.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</p>';
                        }).join('');
                        if (html) {
                            el.innerHTML = html;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('focus', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                            return { ok: true, className: el.className, htmlLen: html.length };
                        }
                    }
                    return { ok: false, reason: 'no_suitable_editor' };
                } catch (e) {
                    return { ok: false, reason: 'js_error', error: e.message };
                }
            }""", text)
            if isinstance(filled, dict) and filled.get("ok"):
                logger.warning("Xiaohongshu content filled (JS fallback, %s, htmlLen=%s)", filled.get("className", "")[:40], filled.get("htmlLen"))
            else:
                logger.warning("Xiaohongshu content editor not found via JS: %s", filled)
        except Exception as e:
            logger.warning("Xiaohongshu JS fill content failed: %s", e)
        page.wait_for_timeout(1500)

    def _click_publish(self, page) -> bool:
        """触发发布。

        XHS 把按钮包在 <xhs-publish-btn> Vue web component (closed shadow DOM) 中。
        按钮无法通过 querySelector 找到。官方方式是用 CustomEvent('publish')
        直接与宿主元素通信，不依赖坐标/按钮选择器。

        参考: xiaohongshu-skills publish.py click_publish_button()
        """
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(500)
        save_screenshot(page, "xhs_before_click_publish")
        save_page_html(page, "xhs_before_click_publish")

        # 注入多层响应捕获（参考 xiaohongshu-skills）
        page.evaluate("""
        (() => {
            window.__xhsPublishResult = null;
            function looksLikePublishResp(body) {
                if (!body) return false;
                return body.includes('HTTPBizError')
                    || /"code":\\s*-913\\d/.test(body)
                    || body.includes('禁止发笔记')
                    || /"note_id":\\s*"[^"]+"/.test(body);
            }
            function capture(source, info) {
                if (window.__xhsPublishResult) return;
                window.__xhsPublishResult = {source, ...info};
            }
            // XHR hook
            const origOpen = XMLHttpRequest.prototype.open;
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__xhsPubUrl = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function() {
                this.addEventListener('loadend', () => {
                    if (!window.__xhsPublishResult && looksLikePublishResp(this.responseText || '')) {
                        try {
                            const j = JSON.parse(this.responseText);
                            capture('xhr', {url: this.__xhsPubUrl, ...j});
                        } catch(e) {
                            capture('xhr', {raw: (this.responseText||'').slice(0,500)});
                        }
                    }
                });
                return origSend.apply(this, arguments);
            };
            // Fetch hook
            const origFetch = window.fetch;
            window.fetch = async function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0].url;
                const resp = await origFetch.apply(this, args);
                try {
                    const txt = await resp.clone().text();
                    if (!window.__xhsPublishResult && looksLikePublishResp(txt)) {
                        try { capture('fetch', {url, ...JSON.parse(txt)}); } catch(e) {
                            capture('fetch', {url, raw: txt.slice(0,500)});
                        }
                    }
                } catch(e) {}
                return resp;
            };
            // Console hook
            ['error', 'log', 'warn'].forEach((method) => {
                const orig = console[method];
                console[method] = function(...args) {
                    const txt = args.map(a => typeof a === 'string' ? a : JSON.stringify(a)).join(' ');
                    if (txt.includes('HTTPBizError') || txt.includes('发布失败')) {
                        const codeMatch = txt.match(/"code":\\s*(-?\\d+)/);
                        const msgMatch = txt.match(/"msg":\\s*"([^"]+)"/);
                        capture('console', {code: codeMatch ? parseInt(codeMatch[1],10) : null, msg: msgMatch ? msgMatch[1] : '发布失败'});
                    }
                    return orig.apply(this, args);
                };
            });
        })()
        """)

        # dispatchEvent('publish') 触发发布
        result = page.evaluate("""
        (() => {
            const host = document.querySelector('xhs-publish-btn[is-publish="true"]');
            if (!host) return 'not_found';
            if (host.getAttribute('submit-disabled') === 'true') return 'disabled';
            host.dispatchEvent(new CustomEvent('publish', {bubbles: true, cancelable: true}));
            return 'fired';
        })()
        """)
        logger.warning("dispatchEvent('publish') result: %s", result)

        if result == "not_found":
            return False
        if result == "disabled":
            logger.warning("Publish button is disabled (submit-disabled=true)")
            return False

        # 等待发布 API 响应
        import time as _time
        deadline = _time.monotonic() + 15
        publish_result = None
        while _time.monotonic() < deadline:
            publish_result = page.evaluate("window.__xhsPublishResult")
            if publish_result:
                break
            page.wait_for_timeout(300)

        # 等 3s 再检查 URL（published=true 表示发布成功）
        page.wait_for_timeout(3000)
        final_url = page.url
        if "published=true" in final_url:
            logger.warning("Publish SUCCESS (published=true in URL)")
            return True

        if publish_result:
            code = publish_result.get("code")
            msg = publish_result.get("msg", "")
            logger.warning("Publish API response: source=%s code=%s msg=%s",
                           publish_result.get("source"), code, msg)
            if code == 0 or publish_result.get("success") is True:
                logger.warning("Publish SUCCESS")
                return True
            elif code is not None and -9140 <= code <= -9130:
                logger.warning("Account risk control: code=%s msg=%s", code, msg)
                return True

        logger.warning("Publish result unclear — assuming fired (no error code)")
        return True

    def _click_save_draft(self, page) -> bool:
        """备选：点击保存草稿按钮"""
        try:
            loc = page.locator('button:has-text("存草稿")').first
            if loc.count() > 0 and loc.is_visible():
                loc.click()
                logger.warning("Xiaohongshu '存草稿' clicked")
                page.wait_for_timeout(2000)
                save_screenshot(page, "xhs_after_save_draft")
                return True
        except Exception as e:
            logger.debug("Save draft button not found: %s", e)
        return False
