"""Publisher 基础测试"""
from pathlib import Path


def test_publish_result_fields():
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from publisher.base import PublishResult
    r = PublishResult(success=True, channel="test", url="http://example.com")
    assert r.success
    assert r.error is None
