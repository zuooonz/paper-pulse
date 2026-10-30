import datetime as dt

from sqlalchemy import select

from api.config import settings
from paper_library.db import get_session
from paper_library.governance_models import SummaryJob
from paper_library.models import ChannelVideo, Job, VideoPublishRecord
from worker import scheduler


def _summary(job_id: str, *, status: str = "running") -> SummaryJob:
    return SummaryJob(
        job_id=job_id,
        fingerprint=f"fingerprint-{job_id}",
        actor_id="local:reviewer",
        actor_json='{"actor_id":"local:reviewer"}',
        paper_ids_json="[1]",
        profile="report",
        model="example-chat-model",
        status=status,
        progress=40,
        stage="正在生成",
        created_at_utc=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2),
        updated_at_utc=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2),
    )


def test_scheduler_queues_due_video_publish_atomically(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    now = dt.datetime.now(dt.UTC)
    with get_session() as session:
        video = ChannelVideo(title="video", status="approved")
        session.add(video)
        session.flush()
        record = VideoPublishRecord(
            video_id=video.id,
            channel="test",
            status="scheduled",
            scheduled_at=now - dt.timedelta(minutes=1),
            created_at=now - dt.timedelta(hours=1),
        )
        session.add(record)
        session.flush()
        record_id = record.id

    assert scheduler._queue_scheduled_video_publishes(settings.workspace, settings.config_dir) == 1

    with get_session() as session:
        record = session.get(VideoPublishRecord, record_id)
        job = session.execute(
            select(Job).where(Job.dedupe_key == f"publish-video-record:{record_id}")
        ).scalar_one()
        assert record.status == "uploading"
        assert job.status == "queued"
        assert job.payload == {"record_id": record_id}


def test_video_publish_record_without_job_is_marked_failed():
    old = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    with get_session() as session:
        video = ChannelVideo(title="video", status="approved")
        session.add(video)
        session.flush()
        record = VideoPublishRecord(
            video_id=video.id,
            channel="test",
            status="uploading",
            created_at=old,
        )
        session.add(record)
        session.flush()
        record_id = record.id

    assert scheduler._reconcile_video_publishes(settings.workspace, settings.config_dir) == 1

    with get_session() as session:
        record = session.get(VideoPublishRecord, record_id)
        assert record.status == "failed"
        assert record.error == "视频发布记录超时且对应任务不存在"


def test_failed_summary_queue_marks_running_summary_terminal():
    with get_session() as session:
        summary = _summary("failed-summary")
        session.add(summary)
        session.add(Job(
            id="failed-summary-queue",
            kind="summary_job",
            payload={"summary_job_id": summary.job_id},
            status="failed",
            priority=0,
            run_after=dt.datetime.now(dt.UTC),
            attempts=3,
            max_attempts=3,
            created_at=dt.datetime.now(dt.UTC),
            updated_at=dt.datetime.now(dt.UTC),
            dedupe_key=f"summary-job:{summary.job_id}",
        ))

    assert scheduler._reconcile_summary_jobs(settings.workspace, settings.config_dir) == 1

    with get_session() as session:
        summary = session.query(SummaryJob).filter_by(job_id="failed-summary").one()
        assert summary.status == "failed"
        assert summary.error_code == "JOB_FAILED"
        assert summary.error_message == "总结任务未能完成，请重新提交"


def test_summary_without_queue_is_reenqueued():
    with get_session() as session:
        session.add(_summary("orphan-summary", status="queued"))

    assert scheduler._reconcile_summary_jobs(settings.workspace, settings.config_dir) == 1

    with get_session() as session:
        summary = session.query(SummaryJob).filter_by(job_id="orphan-summary").one()
        job = session.query(Job).filter_by(dedupe_key="summary-job:orphan-summary").one()
        assert summary.status == "queued"
        assert summary.stage == "任务已恢复排队"
        assert job.status == "queued"
        assert job.payload == {"summary_job_id": "orphan-summary"}
