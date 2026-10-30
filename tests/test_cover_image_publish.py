import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

def test_csdn_publisher_prepends_cover_image(tmp_path):
    from unittest import mock
    from publisher.csdn import CsdnPublisher
    img = tmp_path / "cover.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    pub = CsdnPublisher(cookie="test_cookie")

    mock_p = mock.MagicMock()
    mock_browser = mock.MagicMock()
    mock_context = mock.MagicMock()
    mock_page = mock.MagicMock()
    mock_page.url = "https://blog.csdn.net/user/article/details/12345678"

    def _locator_side_effect(selector):
        m = mock.MagicMock()
        m.count.return_value = 0
        m.first = mock.MagicMock()
        m.first.is_visible.return_value = False
        return m
    mock_page.locator.side_effect = _locator_side_effect
    mock_page.evaluate.return_value = True

    with mock.patch("playwright.sync_api.sync_playwright") as mock_playwright:
        mock_playwright.return_value.__enter__ = mock.MagicMock(return_value=mock_p)
        mock_playwright.return_value.__exit__ = mock.MagicMock(return_value=False)
        mock_p.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        result = pub.publish(draft_id=3, title="T", content="## C", cover_images=[str(img)])

    assert result.success
    assert result.channel == "csdn"
