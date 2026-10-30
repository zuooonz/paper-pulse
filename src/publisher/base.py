"""Publisher 抽象基类"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PublishResult:
    success: bool
    channel: str
    url: Optional[str] = None
    error: Optional[str] = None
    raw: dict = field(default_factory=dict)


class BasePublisher(ABC):
    channel_name: str = "base"

    @abstractmethod
    def publish(self, draft_id: int, title: str, content: str, **kwargs) -> PublishResult:
        """发布一篇草稿，返回 PublishResult

        kwargs 中可能包含:
            check_cancel: Callable[[], bool] — 取消检查回调，返回 True 表示已被取消
        """
        ...

    def _check_cancelled(self, check_cancel) -> Optional[PublishResult]:
        """检查是否已被用户取消。如果已取消，返回取消结果；否则返回 None。"""
        if check_cancel is not None and check_cancel():
            return PublishResult(
                success=False,
                channel=self.channel_name,
                error="已取消",
            )
        return None

    def is_available(self) -> bool:
        """返回该渠道是否已配置好凭证，可以发布"""
        return True

    def process_body_images(
        self,
        content: str,
        image_paths: list[str],
        image_doi_map: dict[str, str] | None = None,
    ) -> str:
        """
        将本地图片路径嵌入正文内容（平台-specific 实现）。
        默认实现：在正文末尾追加图片 markdown 占位符 + 图注。
        子类应覆盖此方法以实现实际上传。

        Args:
            content: 正文内容
            image_paths: 图片路径列表（通常 cover_images[1:] 作为正文配图）
            image_doi_map: 图片路径 -> DOI 的映射，用于生成图注
        """
        if not image_paths or len(image_paths) <= 1:
            return content
        blocks: list[str] = []
        for p in image_paths[1:]:
            blocks.append(f"![论文配图]({p})")
            doi = (image_doi_map or {}).get(p)
            if doi:
                blocks.append(f"> （引自：{doi}）")
        return content + "\n\n" + "\n".join(blocks)
