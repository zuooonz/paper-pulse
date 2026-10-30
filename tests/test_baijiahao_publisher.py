"""百家号 publisher 单元测试"""
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

    def _evaluate_side_effect(script, *args):
        # 模拟 JS evaluate 返回合适的字典
        if not isinstance(script, str):
            return True
        s = script
        if "found" in s and "return" in s:
            return {"found": True, "text": "发布", "tag": "BUTTON"}
        if "success" in s or ("error" in s and "UE" in s):
            return {"success": True, "method": "mock"}
        if "news-title-input" in s:
            return {"found": True, "method": "testid"}
        if "document.body" in s and "innerText" in s:
            return "发布成功"
        if "ant-message" in s or "ant-notification" in s:
            return "发布成功"
        if "querySelectorAll" in s and "button" in s:
            return {"found": True, "text": "发布", "tag": "BUTTON"}
        return True

    mock_page.locator.side_effect = _locator_side_effect
    mock_page.evaluate.side_effect = _evaluate_side_effect
    return mock_page


def test_unavailable_without_cookie():
    from publisher.baijiahao import BaijiahaoPublisher
    pub = BaijiahaoPublisher(cookie="")
    assert not pub.is_available()


def test_available_with_cookie():
    from publisher.baijiahao import BaijiahaoPublisher
    pub = BaijiahaoPublisher(cookie="fake_cookie")
    assert pub.is_available()


def test_publish_fails_without_cookie():
    from publisher.baijiahao import BaijiahaoPublisher
    pub = BaijiahaoPublisher(cookie="")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert r.error


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_success_with_mock(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://baijiahao.baidu.com/builder/rc/edit?type=news")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.baijiahao import BaijiahaoPublisher
    pub = BaijiahaoPublisher(cookie="test_cookie")
    r = pub.publish(1, "Test Article", "## Hello qMRI")
    assert r.success
    assert r.channel == "baijiahao"


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_login_redirect(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://passport.baidu.com/login")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.baijiahao import BaijiahaoPublisher
    pub = BaijiahaoPublisher(cookie="bad_cookie")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert "expired" in r.error.lower() or "失效" in r.error
