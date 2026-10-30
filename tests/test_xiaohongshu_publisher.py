"""小红书 publisher 单元测试"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_mock_page(url: str):
    mock_page = mock.MagicMock()
    mock_page.url = url

    def _locator_side_effect(selector):
        m = mock.MagicMock()
        m.count.return_value = 0
        m.first = mock.MagicMock()
        m.first.is_visible.return_value = False
        return m

    mock_page.locator.side_effect = _locator_side_effect

    def _evaluate_side_effect(script, *args):
        s = script if isinstance(script, str) else ""
        if "window.__xhsPublishResult" in s:
            return {"code": 0, "msg": "ok", "source": "mock", "success": True}
        # 发布按钮坐标探测
        if "found" in s and "x:" in s and "y:" in s:
            return {"found": True, "x": 100, "y": 100, "width": 50, "height": 30, "tag": "BUTTON", "disabled": False}
        # 发布按钮诊断
        if "buttons" in s and "errors" in s:
            return {"buttons": [{"disabled": False, "pointerEvents": "auto", "opacity": "1", "y": 500, "width": 80, "height": 36}], "errors": []}
        # 审核拦截检测 — 无拦截
        if "keywords" in s and "社区规范" in s:
            return None
        # page_text 检测 — 包含成功关键字
        if "document.body" in s and "innerText" in s:
            return "发布成功"
        # 编辑器内容填充 JS fallback
        if "tiptap" in s or "ProseMirror" in s:
            return {"ok": True, "className": "tiptap ProseMirror"}
        return True

    mock_page.evaluate.side_effect = _evaluate_side_effect
    return mock_page


def test_unavailable_without_cookie():
    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="")
    assert not pub.is_available()


def test_available_with_cookie():
    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="fake_cookie")
    assert pub.is_available()


def test_publish_fails_without_cookie():
    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert r.error


def test_publish_fails_without_images():
    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="test_cookie")
    r = pub.publish(1, "title", "content", cover_images=None)
    assert not r.success
    assert "图片" in r.error


def test_playwright_fallback_when_patchright_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "patchright", None)

    from playwright.sync_api import sync_playwright
    from publisher.xiaohongshu import _get_sync_playwright

    assert _get_sync_playwright() is sync_playwright


@mock.patch("publisher.xiaohongshu._get_sync_playwright")
def test_publish_success_with_mock(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://www.xiaohongshu.com/explore/abc123")

    mock_playwright.return_value.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="test_cookie")
    # Mock _upload_images to bypass file existence check
    with mock.patch.object(pub, '_upload_images', return_value=True):
        r = pub.publish(1, "Test Article", "## Hello qMRI", cover_images=["fake.png"])
    assert r.success
    assert r.channel == "xiaohongshu"


@mock.patch("publisher.xiaohongshu._get_sync_playwright")
def test_publish_login_redirect(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://creator.xiaohongshu.com/login")

    mock_playwright.return_value.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.xiaohongshu import XiaohongshuPublisher
    pub = XiaohongshuPublisher(cookie="bad_cookie")
    r = pub.publish(1, "title", "content", cover_images=["fake.png"])
    assert not r.success
    assert "失效" in r.error
