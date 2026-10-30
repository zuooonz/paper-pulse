"""Publish queue lifecycle and process-isolation regression tests."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services import publishing
from api.services import publisher_runtime
from paper_library.models import ArticleDraft, ChannelAccount, DraftBase, Job, PublishRecord


def _draft_session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'drafts.db'}")
    DraftBase.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _session_context(factory):
    @contextmanager
    def _get_session(*args, **kwargs):
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _get_session


def test_cancelled_record_cannot_be_overwritten(monkeypatch, tmp_path):
    factory = _draft_session_factory(tmp_path)
    with factory() as session:
        record = PublishRecord(
            draft_id=1,
            account_id=1,
            channel="zhihu",
            account_name="test",
            status="cancelled",
        )
        session.add(record)
        session.commit()
        record_id = record.id

    monkeypatch.setattr(publishing, "get_session", _session_context(factory))
    with publishing.get_session() as session:
        changed = publishing.transition_publish_record(
            session, record_id, "failed", error="late child-process result"
        )
    assert changed is False

    with factory() as session:
        record = session.get(PublishRecord, record_id)
        assert record.status == "cancelled"
        assert record.error is None


def test_unknown_channel_returns_from_isolated_process():
    result = publisher_runtime.run_publisher_isolated(
        account={
            "id": 999,
            "channel": "unsupported-test-channel",
            "account_name": "test",
            "credential_type": "cookie",
            "credential": "",
        },
        draft_id=1,
        title="test",
        content="test",
        cover_images=[],
        image_doi_map={},
        is_cancelled=lambda: False,
        timeout_seconds=10,
    )
    assert result["success"] is False
    assert result["error"] == "Publisher not implemented for this channel"


def test_publish_record_and_job_share_one_transaction(monkeypatch):
    from fastapi.testclient import TestClient

    from api.dependencies import require_reviewer
    from api.main import app
    from paper_library.db import get_session
    from worker import queue as queue_module

    monkeypatch.setenv("JOBS_INLINE", "false")
    with get_session() as session:
        draft = ArticleDraft(
            draft_type="review", title="Transaction test", content="Body", status="reviewed"
        )
        account = ChannelAccount(
            channel="example", account_name="test account", credential_type="cookie", is_active=True
        )
        session.add_all([draft, account])
        session.flush()
        draft_id, account_id = draft.id, account.id

    original_enqueue = queue_module.enqueue

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue insertion failed")

    app.dependency_overrides[require_reviewer] = lambda: {"username": "reviewer", "role": "reviewer"}
    try:
        monkeypatch.setattr(queue_module, "enqueue", fail_enqueue)
        with TestClient(app, raise_server_exceptions=False) as client:
            failed_response = client.post(
                f"/api/drafts/{draft_id}/publish", json={"account_ids": [account_id]}
            )
        assert failed_response.status_code == 500
        with get_session() as session:
            assert session.query(PublishRecord).filter_by(draft_id=draft_id).count() == 0
            assert session.query(Job).filter(Job.kind == "publish_draft").count() == 0

        monkeypatch.setattr(queue_module, "enqueue", original_enqueue)
        with TestClient(app) as client:
            response = client.post(
                f"/api/drafts/{draft_id}/publish", json={"account_ids": [account_id]}
            )
    finally:
        app.dependency_overrides.pop(require_reviewer, None)

    assert response.status_code == 200
    task = response.json()["tasks"][0]
    with get_session() as session:
        record = session.get(PublishRecord, task["record_id"])
        job = session.get(Job, task["job_id"])
        assert record.status == "pending"
        assert job.payload["record_id"] == record.id
