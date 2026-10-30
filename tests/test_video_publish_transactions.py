import pytest

from api.routers import videos
from paper_library.db import get_session
from paper_library.models import ChannelAccount, ChannelVideo, Job, VideoPublishRecord


def test_video_record_rolls_back_when_queue_insert_fails(monkeypatch, tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")
    with get_session() as session:
        video = ChannelVideo(title="Video", file_path=str(media), status="draft")
        account = ChannelAccount(
            channel="test-channel",
            account_name="test account",
            credential_type="cookie",
            credential="encrypted-placeholder",
            is_active=True,
        )
        session.add_all([video, account])
        session.flush()
        video_id, account_id = video.id, account.id

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr("worker.queue.enqueue", fail_enqueue)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        videos.publish_video(video_id, [account_id], user={"role": "reviewer"})

    with get_session() as session:
        assert session.query(VideoPublishRecord).filter_by(video_id=video_id).count() == 0
        assert session.query(Job).filter(Job.kind == "publish_video").count() == 0
