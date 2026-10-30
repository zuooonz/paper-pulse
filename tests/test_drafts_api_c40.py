"""TestClient contract tests for draft editing, queueing, and scheduling.

These are intentionally kept out of the sandbox run because Starlette's
TestClient can hang there; the normal external pytest run executes them.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from api.dependencies import get_current_user, require_reviewer
from api.main import app
from paper_library.db import get_session
from paper_library.models import ArticleDraft, ChannelAccount, Job, PublishRecord


@pytest.fixture
def reviewer_client():
    reviewer = {"username": "c40-reviewer", "role": "reviewer", "name": "Reviewer"}
    # Every role check builds on get_current_user, so override both.
    app.dependency_overrides[get_current_user] = lambda: reviewer
    app.dependency_overrides[require_reviewer] = lambda: reviewer
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_reviewer, None)


def test_word_upload_edit_and_approve_draft(reviewer_client, tmp_path, monkeypatch):
    from api.services import draft_uploads

    monkeypatch.setattr(
        draft_uploads,
        "parse_docx",
        lambda *_args, **_kwargs: {
            "title": "Synthetic uploaded draft",
            "content": "Synthetic parsed content",
            "image_rel_paths": [],
            "images": [],
        },
    )
    response = reviewer_client.post(
        "/api/drafts/upload-word",
        files={"file": ("synthetic.docx", b"synthetic docx bytes", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 200
    draft_id = response.json()["draft_id"]
    assert response.json()["title"] == "Synthetic uploaded draft"

    edited = reviewer_client.patch(
        f"/api/drafts/{draft_id}",
        json={
            "title": "Edited synthetic title",
            "content": "Edited synthetic content",
            "status": "approved",
            "review_notes": "Reviewed with a synthetic fixture",
            "selected_images": ["data/images/synthetic.png"],
        },
    )
    assert edited.status_code == 200
    saved = reviewer_client.get(f"/api/drafts/{draft_id}")
    assert saved.status_code == 200
    assert saved.json()["title"] == "Edited synthetic title"
    assert saved.json()["content"] == "Edited synthetic content"
    assert saved.json()["status"] == "approved"
    assert saved.json()["review_notes"] == "Reviewed with a synthetic fixture"

    with get_session() as session:
        draft = session.get(ArticleDraft, draft_id)
        assert draft.reviewer == "c40-reviewer"


def test_publish_idempotency_batch_publish_and_scheduling(reviewer_client, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    with get_session() as session:
        drafts = [
            ArticleDraft(draft_type="review", title=f"Synthetic draft {index}", content="Synthetic body")
            for index in range(3)
        ]
        account = ChannelAccount(
            channel="csdn",
            account_name="Synthetic channel account",
            credential_type="cookie",
            is_active=True,
        )
        session.add_all([*drafts, account])
        session.flush()
        draft_ids = [draft.id for draft in drafts]
        account_id = account.id

    first = reviewer_client.post(
        f"/api/drafts/{draft_ids[0]}/publish",
        json={"account_ids": [account_id]},
        headers={"Idempotency-Key": "c40-single-publish"},
    )
    repeated = reviewer_client.post(
        f"/api/drafts/{draft_ids[0]}/publish",
        json={"account_ids": [account_id]},
        headers={"Idempotency-Key": "c40-single-publish"},
    )
    assert first.status_code == repeated.status_code == 200
    assert first.json()["tasks"][0]["record_id"] == repeated.json()["tasks"][0]["record_id"]
    assert first.json()["tasks"][0]["job_id"] == repeated.json()["tasks"][0]["job_id"]

    batch_body = {
        "draft_ids": [draft_ids[1], draft_ids[2]],
        "account_ids": [account_id],
    }
    batch = reviewer_client.post("/api/drafts/batch-publish", json=batch_body, headers={"Idempotency-Key": "c40-batch-publish"})
    batch_again = reviewer_client.post("/api/drafts/batch-publish", json=batch_body, headers={"Idempotency-Key": "c40-batch-publish"})
    assert batch.status_code == batch_again.status_code == 200
    assert [task["record_id"] for task in batch.json()["tasks"]] == [
        task["record_id"] for task in batch_again.json()["tasks"][:2]
    ]

    scheduled = reviewer_client.post(
        f"/api/drafts/{draft_ids[0]}/schedule",
        json={"account_ids": [account_id], "scheduled_at": "2030-04-05T10:30"},
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["account_count"] == 1

    batch_schedule = reviewer_client.post(
        "/api/drafts/batch-schedule",
        json={
            "draft_ids": draft_ids[1:],
            "account_ids": [account_id],
            "start_at": "2030-04-06T09:00",
            "interval_hours": 6,
            "jitter_hours": 0,
        },
    )
    assert batch_schedule.status_code == 200
    assert batch_schedule.json()["scheduled_records"] == 2

    with get_session() as session:
        assert session.query(PublishRecord).filter(PublishRecord.status == "pending").count() == 3
        assert session.query(PublishRecord).filter(PublishRecord.status == "scheduled").count() == 3
        assert session.query(Job).filter(Job.kind == "publish_draft", Job.status == "queued").count() == 3


def test_publish_and_schedule_require_at_least_one_target(reviewer_client):
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="No target", content="body")
        session.add(draft)
        session.flush()
        draft_id = draft.id

    calls = [
        (f"/api/drafts/{draft_id}/publish", {"account_ids": []}),
        ("/api/drafts/batch-publish", {"draft_ids": [draft_id]}),
        (f"/api/drafts/{draft_id}/schedule", {"scheduled_at": "2030-04-05T10:30"}),
        ("/api/drafts/batch-schedule", {"draft_ids": [draft_id], "start_at": "2030-04-05T10:30"}),
    ]
    for path, payload in calls:
        response = reviewer_client.post(path, json=payload)
        assert response.status_code == 400


def test_schedule_validation(reviewer_client):
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Scheduled", content="body")
        account = ChannelAccount(
            channel="csdn", account_name="Schedule account", credential_type="cookie", is_active=True
        )
        session.add_all([draft, account])
        session.flush()
        draft_id, account_id = draft.id, account.id

    bad_date = reviewer_client.post(
        f"/api/drafts/{draft_id}/schedule",
        json={"account_ids": [account_id], "scheduled_at": "not-a-datetime"},
    )
    assert bad_date.status_code == 400
    inactive_account = reviewer_client.post(
        f"/api/drafts/{draft_id}/schedule",
        json={"account_ids": [999999], "scheduled_at": "2030-04-05T10:30"},
    )
    assert inactive_account.status_code == 400

    scheduled = reviewer_client.post(
        f"/api/drafts/{draft_id}/schedule",
        json={"account_ids": [account_id], "scheduled_at": "2030-04-05T10:30"},
    )
    assert scheduled.status_code == 200
    with get_session() as session:
        assert session.query(PublishRecord).filter_by(draft_id=draft_id, status="scheduled").count() == 1


def test_batch_schedule_rejects_missing_draft_and_negative_intervals(reviewer_client):
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Batch schedule", content="body")
        account = ChannelAccount(
            channel="csdn", account_name="Batch account", credential_type="cookie", is_active=True
        )
        session.add_all([draft, account])
        session.flush()
        draft_id, account_id = draft.id, account.id

    missing = reviewer_client.post(
        "/api/drafts/batch-schedule",
        json={
            "draft_ids": [draft_id, 999999],
            "account_ids": [account_id],
            "start_at": "2030-04-06T09:00",
        },
    )
    assert missing.status_code == 400
    invalid_interval = reviewer_client.post(
        "/api/drafts/batch-schedule",
        json={
            "draft_ids": [draft_id],
            "account_ids": [account_id],
            "start_at": "2030-04-06T09:00",
            "interval_hours": -1,
        },
    )
    assert invalid_interval.status_code == 400


def test_publish_and_schedule_reject_any_invalid_account_or_draft(reviewer_client):
    with get_session() as session:
        drafts = [ArticleDraft(draft_type="review", title=f"Target {i}", content="body") for i in range(2)]
        account = ChannelAccount(channel="csdn", account_name="Valid target", credential_type="cookie", is_active=True)
        inactive = ChannelAccount(channel="csdn", account_name="Inactive target", credential_type="cookie", is_active=False)
        session.add_all([*drafts, account, inactive])
        session.flush()
        draft_id, other_draft_id = drafts[0].id, drafts[1].id
        account_id, inactive_id = account.id, inactive.id

    invalid_targets = [account_id, inactive_id, 999999]
    publish_calls = [
        (f"/api/drafts/{draft_id}/publish", {"account_ids": invalid_targets}),
        ("/api/drafts/batch-publish", {"draft_ids": [draft_id, other_draft_id], "account_ids": invalid_targets}),
        (f"/api/drafts/{draft_id}/schedule", {"account_ids": invalid_targets, "scheduled_at": "2030-04-05T10:30"}),
        ("/api/drafts/batch-schedule", {"draft_ids": [draft_id, other_draft_id], "account_ids": invalid_targets, "start_at": "2030-04-05T10:30"}),
    ]
    for path, payload in publish_calls:
        response = reviewer_client.post(path, json=payload)
        assert response.status_code == 400
        assert "999999" in response.json()["detail"]

    missing_publish = reviewer_client.post(
        "/api/drafts/batch-publish", json={"draft_ids": [draft_id, 999999], "account_ids": [account_id]}
    )
    missing_schedule = reviewer_client.post(
        "/api/drafts/batch-schedule", json={"draft_ids": [draft_id, 999999], "account_ids": [account_id], "start_at": "2030-04-05T10:30"}
    )
    assert missing_publish.status_code == missing_schedule.status_code == 400
    assert "999999" in missing_publish.json()["detail"]
    assert "999999" in missing_schedule.json()["detail"]


def test_patch_rejects_published_and_unknown_status(reviewer_client):
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Transition", content="body")
        session.add(draft)
        session.flush()
        draft_id = draft.id

    assert reviewer_client.patch(f"/api/drafts/{draft_id}", json={"status": "published"}).status_code == 400
    assert reviewer_client.patch(f"/api/drafts/{draft_id}", json={"status": "mystery"}).status_code == 400


def test_patch_delete_require_reviewer_roles():
    with get_session() as session:
        drafts = [ArticleDraft(draft_type="review", title=f"Role {role}", content="body") for role in ("viewer", "limited", "reviewer")]
        session.add_all(drafts)
        session.flush()
        ids = [draft.id for draft in drafts]

    try:
        for role, draft_id in zip(("viewer", "limited"), ids[:2]):
            app.dependency_overrides[get_current_user] = lambda role=role: {"username": f"c50-{role}", "role": role}
            with TestClient(app) as client:
                assert client.patch(f"/api/drafts/{draft_id}", json={"title": "Forbidden"}).status_code == 403
                assert client.delete(f"/api/drafts/{draft_id}").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {"username": "c50-reviewer", "role": "reviewer"}
        with TestClient(app) as client:
            assert client.patch(f"/api/drafts/{ids[2]}", json={"title": "Reviewer edit"}).status_code == 200
            assert client.delete(f"/api/drafts/{ids[2]}").status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_reviewer, None)
