"""Durable queue behavior on SQLite and optional PostgreSQL claim concurrency."""

import datetime as dt
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from paper_library.db import get_session
from paper_library.models import ArticleDraft, Job, PublishRecord
from worker.queue import (
    claim_next,
    cancel,
    complete,
    enqueue,
    enqueue_exclusive,
    fail,
    get_job,
    reclaim_expired,
)
from worker.registry import HANDLERS
from worker import scheduler as scheduler_module
from worker.scheduler import _reconcile_pending_publishes, acquire_lease


def test_enqueue_claim_and_complete(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("unit", {"value": 7})

    claimed = claim_next("test-worker", ["unit"])
    assert claimed["id"] == job_id
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert complete(job_id, {"answer": 42}, worker_id="test-worker")

    saved = get_job(job_id)
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"answer": 42}
    assert saved["locked_by"] is None


def test_fail_requeues_with_backoff_then_exhausts_attempts(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("retry", {}, max_attempts=2)
    claim_next("worker-1", ["retry"])
    assert fail(job_id, "temporary", worker_id="worker-1", backoff_seconds=0)

    retry = get_job(job_id)
    assert retry["status"] == "queued"
    assert retry["error"] == "temporary"
    assert claim_next("worker-2", ["retry"])["attempts"] == 2
    assert fail(job_id, "permanent", worker_id="worker-2")
    assert get_job(job_id)["status"] == "failed"


def test_cancel_queued_job(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("cancel", {})
    assert cancel(job_id) is True
    assert get_job(job_id)["status"] == "cancelled"
    assert claim_next("worker", ["cancel"]) is None


def test_expired_heartbeat_requeues_running_job(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("lease", {})
    claim_next("worker", ["lease"])
    with get_session() as session:
        row = session.get(Job, job_id)
        row.heartbeat_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
        row.locked_at = row.heartbeat_at

    assert reclaim_expired(lease_timeout=1) == 1
    saved = get_job(job_id)
    assert saved["status"] == "queued"
    assert saved["locked_by"] is None
    assert saved["heartbeat_at"] is None


@pytest.mark.parametrize("recover_via_claim", [False, True])
def test_expired_job_at_attempt_limit_becomes_terminal(monkeypatch, recover_via_claim):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("crash-loop", {}, max_attempts=1)
    assert claim_next("crashing-worker", ["crash-loop"])["attempts"] == 1
    with get_session() as session:
        row = session.get(Job, job_id)
        row.heartbeat_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)
        row.locked_at = row.heartbeat_at

    if recover_via_claim:
        assert claim_next("replacement-worker", ["crash-loop"]) is None
    else:
        assert reclaim_expired(lease_timeout=1) == 1

    saved = get_job(job_id)
    assert saved["status"] == "failed"
    assert saved["attempts"] == saved["max_attempts"] == 1
    assert saved["error"] == "Worker 租约超时，任务已达到最大尝试次数"
    assert saved["locked_by"] is None


def test_enqueue_deduplicates_key(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    first = enqueue("dedupe", {"value": 1}, dedupe_key="same-work")
    second = enqueue("dedupe", {"value": 2}, dedupe_key="same-work")
    assert first == second
    assert get_job(first)["payload"] == {"value": 1}


def test_enqueue_uses_caller_transaction(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = None

    with pytest.raises(RuntimeError, match="rollback"):
        with get_session() as session:
            job_id = enqueue("transactional", {}, session=session, inline=False)
            assert session.get(Job, job_id) is not None
            raise RuntimeError("rollback")

    assert job_id is not None
    assert get_job(job_id) is None


def test_scheduler_requeues_old_pending_publish_records_idempotently(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    old_created = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=20)

    with get_session() as session:
        session.add_all([
            ArticleDraft(id=draft_id, draft_type="review", title=f"Draft {draft_id}", content="body")
            for draft_id in (101, 102, 103)
        ])
        session.flush()
        orphan = PublishRecord(
            draft_id=101, account_id=None, channel="test", account_name="orphan",
            status="pending", created_at=old_created,
        )
        active = PublishRecord(
            draft_id=102, account_id=None, channel="test", account_name="active",
            status="pending", created_at=old_created,
        )
        failed = PublishRecord(
            draft_id=103, account_id=None, channel="test", account_name="failed",
            status="pending", created_at=old_created,
        )
        session.add_all([orphan, active, failed])
        session.flush()
        active_job_id = enqueue(
            "publish_draft", {"record_id": active.id},
            dedupe_key=f"publish-draft-record:{active.id}", session=session, inline=False,
        )
        failed_job_id = enqueue(
            "publish_draft", {"record_id": failed.id},
            dedupe_key=f"publish-draft-record:{failed.id}", session=session, inline=False,
        )
        session.get(Job, failed_job_id).status = "failed"
        orphan_id = orphan.id

    assert _reconcile_pending_publishes(".", "config") == 2
    orphan_job = get_job_by_dedupe(f"publish-draft-record:{orphan_id}")
    assert orphan_job["status"] == "queued"
    assert get_job(active_job_id)["status"] == "queued"
    failed_job = get_job(failed_job_id)
    assert failed_job["status"] == "queued"
    assert failed_job["attempts"] == 0
    assert _reconcile_pending_publishes(".", "config") == 0


def get_job_by_dedupe(dedupe_key):
    from worker.queue import find_by_dedupe_key

    return find_by_dedupe_key(dedupe_key)


def test_inline_enqueue_runs_registered_handler(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "true")
    monkeypatch.setitem(HANDLERS, "inline-test", lambda payload, job_id: {"seen": payload["value"]})
    job_id = enqueue("inline-test", {"value": 9})
    saved = get_job(job_id)
    assert saved["status"] == "succeeded"
    assert saved["result"] == {"seen": 9}


def test_inline_transient_failure_respects_max_attempts(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "true")

    def transient_failure(_payload, _job_id):
        raise RuntimeError("temporary failure")

    monkeypatch.setitem(HANDLERS, "inline-retry-test", transient_failure)
    job_id = enqueue("inline-retry-test", {}, max_attempts=2)

    saved = get_job(job_id)
    assert saved["status"] == "queued"
    assert saved["attempts"] == 1
    assert saved["max_attempts"] == 2


def test_inline_permanent_failure_does_not_retry(monkeypatch):
    from worker.registry import PermanentJobError

    monkeypatch.setenv("JOBS_INLINE", "true")

    def permanent_failure(_payload, _job_id):
        raise PermanentJobError("invalid job payload")

    monkeypatch.setitem(HANDLERS, "inline-permanent-test", permanent_failure)
    job_id = enqueue("inline-permanent-test", {}, max_attempts=3)

    saved = get_job(job_id)
    assert saved["status"] == "failed"
    assert saved["attempts"] == 1


def test_exclusive_enqueue_allows_only_one_active_job(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    first, created = enqueue_exclusive("singleton", {"value": 1})
    second, duplicate_created = enqueue_exclusive("singleton", {"value": 2})
    assert created is True
    assert duplicate_created is False
    assert second == first
    claim_next("singleton-worker", ["singleton"])
    complete(first, {"done": True}, worker_id="singleton-worker")
    third, created_again = enqueue_exclusive("singleton", {"value": 3})
    assert created_again is True
    assert third != first


def test_scheduler_lease_has_single_owner():
    assert acquire_lease("scheduler-one", lease_seconds=30) is True
    assert acquire_lease("scheduler-two", lease_seconds=30) is False


def test_scheduler_stops_tick_after_lease_loss(monkeypatch):
    from api import scheduler as api_scheduler
    from paper_library import governance

    lease_lost = threading.Event()
    calls = []
    monkeypatch.setattr(governance, "release_stale_reservations", lambda: (lease_lost.set(), 0)[1])
    monkeypatch.setattr(governance, "expire_access_requests", lambda: calls.append("expire"))
    monkeypatch.setattr(api_scheduler, "reset_stale_paper_states", lambda *_args: calls.append("reset"))

    scheduler_module.scheduler_tick(".", "config", leadership_lost=lease_lost)

    assert calls == []


def test_scheduler_lease_renewal_signals_lost_leadership(monkeypatch):
    lease_lost = threading.Event()
    monkeypatch.setattr(scheduler_module, "RENEW_SECONDS", 0)
    monkeypatch.setattr(scheduler_module, "acquire_lease", lambda _owner: False)

    scheduler_module._renew_lease_until("scheduler-one", threading.Event(), lease_lost)

    assert lease_lost.is_set()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").startswith(("postgresql://", "postgresql+")),
                    reason="requires TEST_DATABASE_URL with PostgreSQL")
def test_postgres_skip_locked_claims_distinct_jobs(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    first = enqueue("parallel", {"n": 1})
    second = enqueue("parallel", {"n": 2})
    locked = threading.Event()
    release = threading.Event()

    def hold_first_row_lock():
        with get_session() as session:
            session.query(Job).filter(Job.id == first).with_for_update().one()
            locked.set()
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_first_row_lock)
        assert locked.wait(10)
        claimed = claim_next("parallel-worker", ["parallel"])
        release.set()
        holder.result(timeout=10)

    assert claimed["id"] == second
    assert get_job(first)["status"] == "queued"
