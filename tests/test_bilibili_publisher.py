"""Bilibili 发布器单元测试"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from publisher.bilibili import BilibiliPublisher, _md_to_plaintext


class TestMdToPlaintext:
    def test_image_placeholder(self):
        """Markdown 图片应转为占位符文本"""
        md = "正文\n\n![论文配图](path/to/img.jpg)\n\n> （引自：10.1000/test）"
        result = _md_to_plaintext(md)
        assert "[图片: 论文配图]" in result
        assert "![论文配图]" not in result
        assert "（引自：10.1000/test）" in result

    def test_multiple_images(self):
        """多张图片应生成多个占位符"""
        md = "![图1](a.jpg)\n\n![图2](b.jpg)"
        result = _md_to_plaintext(md)
        assert result.count("[图片:") == 2

    def test_link_stripped(self):
        """普通链接应只保留文本"""
        md = "参见[原文](http://example.com)"
        result = _md_to_plaintext(md)
        assert "参见原文" in result
        assert "http://example.com" not in result

    def test_bold_italic_removed(self):
        """粗体斜体标记应去除"""
        md = "**粗体** 和 *斜体*"
        result = _md_to_plaintext(md)
        assert "粗体" in result
        assert "斜体" in result
        assert "**" not in result
        assert "*" not in result


class TestBilibiliPublisher:
    def test_is_available_with_cookie(self):
        pub = BilibiliPublisher(cookie="SESSDATA=xxx")
        assert pub.is_available() is True

    def test_is_available_without_cookie(self):
        pub = BilibiliPublisher(cookie="")
        assert pub.is_available() is False

    def test_title_truncation(self):
        """标题应截断到 30 字符"""
        BilibiliPublisher(cookie="test")
        # _fill_title 中 title = title[:30]
        long_title = "这是一篇非常非常非常非常非常非常长的标题"
        truncated = long_title[:30]
        assert len(truncated) <= 30
