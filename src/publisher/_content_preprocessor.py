"""
跨平台正文预处理策略统一模块

按平台类型分发策略：
- rich_text: 搜狐号、百家号（基于 Quill/UEditor 的富文本编辑器）
- markdown: CSDN（原生 Markdown 编辑器）
- prosemirror: 小红书（ProseMirror/Tiptap 编辑器）
"""
from __future__ import annotations

import re
from typing import Literal, Optional

PlatformType = Literal["rich_text", "markdown", "prosemirror", "html"]


# 各平台字数限制配置（0 表示无限制）
PLATFORM_LIMITS: dict[str, int] = {
    "csdn": 0,        # CSDN 无明确字数限制
    "zhihu": 0,       # 知乎文章无明确字数限制
    "baijiahao": 0,   # 百家号无明确字数限制
    "sohu": 0,        # 搜狐号无明确字数限制
    "xiaohongshu": 1000,  # 小红书正文约 1000 字限制
    "bilibili": 0,        # 哔哩哔哩专栏无明确字数限制
}


class ContentPreprocessor:
    """统一的内容预处理器，按平台类型分发策略"""

    def __init__(self, platform_type: PlatformType, max_length: int = 0):
        self.platform_type = platform_type
        self.max_length = max_length

    def process(self, content: str) -> str:
        """主入口：按平台类型选择预处理器"""
        if self.platform_type == "rich_text":
            text = self._to_rich_text(content)
        elif self.platform_type == "markdown":
            text = self._to_markdown(content)
        elif self.platform_type == "prosemirror":
            text = self._to_prosemirror(content)
        elif self.platform_type == "html":
            text = self._to_html(content)
        else:
            text = content

        if self.max_length > 0 and len(text) > self.max_length:
            text = self._truncate(text, self.max_length)

        return text

    # ───────────────────────────────────────────────
    # rich_text: 搜狐号、百家号
    # 输出适合 keyboard.type 的纯文本，保留段落结构
    # 图片转为占位符文本，不丢失图片引用信息
    # ───────────────────────────────────────────────
    def _to_rich_text(self, content: str) -> str:
        text = content
        # 图片：保留 alt 作为占位符
        text = re.sub(r"!\[(.*?)\]\(.*?\)", r"[图片: \1]", text)
        # 普通链接：保留文本
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        # 删除 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 删除 Markdown 格式标记（标题 # 保留）
        text = re.sub(r"\*\*|__|\*|_|`", "", text)
        # 删除 --- 分隔线
        text = re.sub(r"^---+$", "", text, flags=re.MULTILINE)
        # 合并多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ───────────────────────────────────────────────
    # markdown: CSDN
    # 保持 Markdown 原样，仅做标准化清理
    # ───────────────────────────────────────────────
    def _to_markdown(self, content: str) -> str:
        text = content
        # 标准化换行符
        text = text.replace("\r\n", "\n")
        # 删除首尾空白
        return text.strip()

    # ───────────────────────────────────────────────
    # prosemirror: 小红书
    # Markdown → 纯文本段落，保留标题结构
    # 图片转为占位符
    # ───────────────────────────────────────────────
    def _to_prosemirror(self, content: str) -> str:
        if not content:
            return ""
        text = content
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # 图片：保留 alt 作为占位符
        text = re.sub(r"!\[(.*?)\]\(.*?\)", r"[图片: \1]", text)
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        text = re.sub(r"\*\*\*(.*?)\*\*\*", r"\1", text)
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"__(.*?)__", r"\1", text)
        text = re.sub(r"_(.*?)_", r"\1", text)
        text = re.sub(r"~~(.*?)~~", r"\1", text)
        text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
        text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^---+\s*$", "", text, flags=re.MULTILINE)
        # 章节标题：把 ## 转为 > 符号
        text = re.sub(r"^(#{1,6})\s*(.+)$", lambda m: "> " + m.group(2), text, flags=re.MULTILINE)
        # 合并多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ───────────────────────────────────────────────
    # Markdown → HTML，保留图片引用
    # ───────────────────────────────────────────────
    def _to_html(self, content: str) -> str:
        try:
            import markdown as md_lib
            html = md_lib.markdown(content, extensions=["tables", "fenced_code"])
        except ImportError:
            html = content
        return html

    def _truncate(self, text: str, max_len: int) -> str:
        """按字数截断，保留完整句子"""
        if len(text) <= max_len:
            return text
        # 找最后一个句号/换行截断
        cut = text.rfind("。", 0, max_len)
        if cut == -1:
            cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        return text[:cut] + "……"


def preprocess_for_platform(
    content: str,
    channel: str,
    max_length: Optional[int] = None,
) -> str:
    """便捷函数：按渠道名称自动选择预处理器"""
    channel_type_map: dict[str, PlatformType] = {
        "csdn": "markdown",
        "zhihu": "rich_text",
        "baijiahao": "rich_text",
        "sohu": "rich_text",
        "bilibili": "rich_text",
        "xiaohongshu": "prosemirror",
    }
    ptype = channel_type_map.get(channel, "rich_text")
    limit = max_length if max_length is not None else PLATFORM_LIMITS.get(channel, 0)
    return ContentPreprocessor(ptype, limit).process(content)
