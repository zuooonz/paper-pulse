"""CSDN publisher 单元测试"""
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
    mock_page.evaluate.return_value = True
    return mock_page


def test_is_unavailable_without_cookie():
    from publisher.csdn import CsdnPublisher
    pub = CsdnPublisher(cookie="")
    assert not pub.is_available()


def test_is_available_with_cookie():
    from publisher.csdn import CsdnPublisher
    pub = CsdnPublisher(cookie="fake_cookie")
    assert pub.is_available()


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_fails_without_cookie(mock_playwright):
    from publisher.csdn import CsdnPublisher
    pub = CsdnPublisher(cookie="")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert r.error


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_success(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://blog.csdn.net/user/article/details/12345678")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.csdn import CsdnPublisher
    pub = CsdnPublisher(cookie="test_cookie", author_username="test_user")
    r = pub.publish(1, "Test Article", "## Hello qMRI")
    assert r.success
    assert r.channel == "csdn"


@mock.patch("playwright.sync_api.sync_playwright")
def test_publish_login_redirect(mock_playwright):
    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = _make_mock_page("https://passport.csdn.net/login")

    mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
    mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    from publisher.csdn import CsdnPublisher
    pub = CsdnPublisher(cookie="bad_cookie")
    r = pub.publish(1, "title", "content")
    assert not r.success
    assert "expired" in r.error.lower() or "失效" in r.error
