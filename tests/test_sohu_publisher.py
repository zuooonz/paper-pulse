"""搜狐号 publisher 单元测试"""
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
        # 发布按钮探测
        if "candidates" in s and "publishBtns" in s:
            return {"clicked": True, "target": {"text": "发布"}}
        # 配额检测 — 返回 falsy，避免误判为配额超限
        if "bodyText.includes('已达上限')" in s:
            return None
        # 确认弹窗检测 — 没有弹窗
        if "allDialogs" in s and "确认发布" in s:
            return {"clicked": False}
        return True

    mock_page.evaluate.side_effect = _evaluate_side_effect
    return mock_page


def test_unavailable_without_cookie():
    from publisher.sohu import SohuPublisher
    pub = SohuPublisher(cookie="")
    assert not pub.is_available()


def test_available_with_cookie():
    from publisher.sohu import SohuPublisher
    pub = SohuPublisher(cookie="fake_cookie")
    assert pub.is_available()


def test_publish_fails_without_cookie():
    from publisher.sohu import SohuPublisher
    pub = SohuPublisher(cookie="")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert r.error


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_success_with_mock(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://mp.sohu.com/mpfe/v3/main/news/published")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.sohu import SohuPublisher
    pub = SohuPublisher(cookie="test_cookie")
    r = pub.publish(1, "Test Article", "## Hello qMRI\n\nThis is a test.")
    assert r.success
    assert r.channel == "sohu"


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_login_redirect(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://mp.sohu.com/h5/v4/login")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.sohu import SohuPublisher
    pub = SohuPublisher(cookie="bad_cookie")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert "失效" in r.error
