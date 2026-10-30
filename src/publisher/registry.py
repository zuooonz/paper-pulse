"""Build channel publishers from account credentials or built-in configuration."""
from __future__ import annotations

from .base import BasePublisher


def build_publisher_from_account(account: dict, workspace: str = ".", config_dir: str = "config") -> "BasePublisher | None":
    """
    根据 ChannelAccount dict（含 channel/credential_type/credential）实例化对应 publisher。
    account 是普通 dict（非 ORM 对象）：
    {
      "id": int,
      "channel": str,           # csdn/zhihu/baijiahao/xiaohongshu/sohu/bilibili
      "credential_type": str,   # cookie
      "credential": str,        # cookie字符串
    }
    """
    channel = account.get("channel", "")
    cred = account.get("credential", "") or ""
    if channel == "csdn":
        from .csdn import CsdnPublisher
        return CsdnPublisher(cookie=(cred or "").strip())

    elif channel == "zhihu":
        from .zhihu import ZhihuPublisher
        return ZhihuPublisher(cookie=(cred or "").strip())

    elif channel == "baijiahao":
        from .baijiahao import BaijiahaoPublisher
        return BaijiahaoPublisher(cookie=(cred or "").strip())

    elif channel == "xiaohongshu":
        from .xiaohongshu import XiaohongshuPublisher
        return XiaohongshuPublisher(cookie=(cred or "").strip())

    elif channel == "sohu":
        from .sohu import SohuPublisher
        return SohuPublisher(cookie=(cred or "").strip())

    elif channel == "bilibili":
        from .bilibili import BilibiliPublisher
        return BilibiliPublisher(cookie=(cred or "").strip())

    return None
