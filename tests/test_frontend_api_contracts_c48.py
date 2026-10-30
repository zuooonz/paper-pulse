"""TestClient contracts for response fields consumed by the frontend.

These tests are intended for the external test run. Starlette TestClient may
hang in the local sandbox, so they are not run here.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from api.config import settings
from api.dependencies import get_current_user
from api.main import app
from paper_library.db import get_session, get_engine
from paper_library.models import ArticleDraft, ChannelVideo, Paper, PublishRecord, VideoPublishRecord


def assert_nullable_string(value):
    assert value is None or isinstance(value, str)


@pytest.fixture
def contract_client():
    user = {"username": "c48-contract", "role": "admin", "name": "Contract User"}
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def contract_records():
    now = datetime.datetime.now(datetime.UTC)
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        drafts = [
            ArticleDraft(draft_type="review", title=f"C48 status {status}", content="body", status=status)
            for status in ("draft", "approved", "rejected", "published")
        ]
        session.add_all(drafts)
        session.flush()

        records = [
            PublishRecord(
                draft_id=drafts[3].id,
                channel="wechat",
                account_name="Synthetic account",
                status="success",
                url="https://example.invalid/published",
                error=None,
                published_at=now,
                created_at=now,
            ),
            PublishRecord(
                draft_id=drafts[0].id,
                channel="csdn",
                account_name="Synthetic account",
                status="failed",
                url=None,
                error="Synthetic failure",
                created_at=now - datetime.timedelta(minutes=1),
            ),
        ]
        session.add_all(records)

        video = ChannelVideo(
            title="C48 contract video",
            description="Synthetic description",
            tags="contract",
            file_path="videos/synthetic.mp4",
            status="draft",
            script='{"storyboard":{"scenes":[]}}',
        )
        session.add(video)
        session.flush()
        video_record = VideoPublishRecord(
            video_id=video.id,
            channel="bilibili",
            account_name="Synthetic video account",
            status="success",
            publish_url="https://example.invalid/video",
            published_at=now,
            created_at=now,
        )
        session.add(video_record)
        session.flush()
        ids = {"video_id": video.id}

    return ids


def test_stats_routes_match_frontend_contracts(contract_client, contract_records):
    publish = contract_client.get("/api/stats/publish")
    assert publish.status_code == 200
    publish_data = publish.json()
    for key in ("total", "success", "failed", "scheduled"):
        assert type(publish_data[key]) is int
    assert isinstance(publish_data["by_channel"], list)
    assert isinstance(publish_data["daily_trend"], list)
    assert isinstance(publish_data["recent_records"], list)
    for row in publish_data["by_channel"]:
        assert isinstance(row["channel"], str)
        assert isinstance(row["account_name"], str)
        assert type(row["count"]) is int
    for row in publish_data["daily_trend"]:
        assert isinstance(row["date"], str)
        assert type(row["count"]) is int
    success_record = next(row for row in publish_data["recent_records"] if row["status"] == "success")
    assert isinstance(success_record["draft_title"], str)
    assert success_record["draft_title"] == "C48 status published"
    for key in ("id", "draft_id", "channel", "account_name", "status", "url", "error", "scheduled_at", "published_at", "created_at"):
        assert key in success_record
    assert type(success_record["id"]) is int
    assert type(success_record["draft_id"]) is int
    assert isinstance(success_record["channel"], str)
    assert isinstance(success_record["account_name"], str)
    assert isinstance(success_record["status"], str)
    for key in ("url", "error", "scheduled_at", "published_at", "created_at"):
        assert_nullable_string(success_record[key])

    drafts = contract_client.get("/api/stats/drafts")
    assert drafts.status_code == 200
    draft_data = drafts.json()
    assert type(draft_data["total"]) is int
    assert isinstance(draft_data["distribution"], dict)
    for status in ("draft", "approved", "rejected", "published"):
        assert type(draft_data[status]) is int
        assert type(draft_data["distribution"].get(status, 0)) is int
    assert all(draft_data[status] >= 1 for status in ("draft", "approved", "rejected", "published"))

    video_stats = contract_client.get("/api/stats/video-publish")
    assert video_stats.status_code == 200
    video_data = video_stats.json()
    for key in ("total", "success", "failed", "scheduled"):
        assert type(video_data[key]) is int
    assert isinstance(video_data["by_channel"], list)
    assert isinstance(video_data["daily_trend"], list)
    assert isinstance(video_data["recent_records"], list)
    for row in video_data["by_channel"]:
        assert isinstance(row["channel"], str)
        assert isinstance(row["account_name"], str)
        assert type(row["count"]) is int
    for row in video_data["daily_trend"]:
        assert isinstance(row["date"], str)
        assert type(row["count"]) is int
    if video_data["recent_records"]:
        row = video_data["recent_records"][0]
        for key in ("id", "video_id", "channel", "account_name", "status", "url", "error", "scheduled_at", "published_at", "created_at"):
            assert key in row
        assert type(row["id"]) is int
        assert type(row["video_id"]) is int
        for key in ("channel", "account_name", "status"):
            assert isinstance(row[key], str)
        for key in ("url", "error", "scheduled_at", "published_at", "created_at"):
            assert_nullable_string(row[key])


def test_publish_stats_uses_placeholder_for_deleted_draft(contract_client):
    engine = get_engine(workspace=settings.workspace, config_dir=settings.config_dir)
    if engine.dialect.name != "sqlite":
        pytest.skip("orphaned foreign-key fixture is SQLite-specific")

    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            "INSERT INTO publish_records (draft_id, status, created_at) VALUES (?, ?, ?)",
            (999999, "failed", datetime.datetime.now(datetime.UTC).isoformat()),
        )
        orphan_record_id = cursor.lastrowid
        connection.commit()
        cursor.execute("PRAGMA foreign_keys=ON")
        connection.commit()
    finally:
        connection.close()

    response = contract_client.get("/api/stats/publish")
    assert response.status_code == 200
    record = next(row for row in response.json()["recent_records"] if row["id"] == orphan_record_id)
    assert record["draft_title"] == "（草稿已删除）"


def test_video_detail_includes_frontend_script_field(contract_client, contract_records):
    response = contract_client.get(f"/api/videos/{contract_records['video_id']}")
    assert response.status_code == 200
    video = response.json()
    for key in (
        "id", "title", "description", "tags", "file_path", "file_size", "duration",
        "cover_path", "width", "height", "status", "source_paper_id", "script",
        "created_at", "updated_at", "publish_records",
    ):
        assert key in video
    assert type(video["id"]) is int
    assert isinstance(video["title"], str)
    assert isinstance(video["status"], str)
    for key in ("description", "tags", "file_path", "cover_path", "created_at", "updated_at"):
        assert_nullable_string(video[key])
    for key in ("file_size", "duration", "width", "height", "source_paper_id"):
        assert video[key] is None or isinstance(video[key], (int, float))
    assert video["script"] == '{"storyboard":{"scenes":[]}}'
    assert isinstance(video["publish_records"], list)


def test_paper_detail_returns_editable_accepted_date(contract_client):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        paper = Paper(title="C48 contract paper", accepted_date=datetime.date(2024, 6, 7))
        session.add(paper)
        session.flush()
        paper_id = paper.id

    response = contract_client.get(f"/api/papers/{paper_id}")
    assert response.status_code == 200
    detail = response.json()
    assert "accepted_date" in detail
    assert detail["accepted_date"] == "2024-06-07"


def test_recent_daily_trend_covers_last_30_days_with_zeros():
    import datetime as dt
    from collections import Counter

    from api.routers.stats import _recent_daily_trend

    trend = _recent_daily_trend(Counter({"2026-10-22": 2, "2026-08-01": 5}), today=dt.date(2026, 10, 22))
    assert len(trend) == 30
    assert trend[0] == {"date": "2026-09-23", "count": 0}
    assert trend[-1] == {"date": "2026-10-22", "count": 2}
    assert sum(item["count"] for item in trend) == 2
