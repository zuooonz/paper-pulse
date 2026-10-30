"""Fast, offline branch tests for the durable worker and local core modules."""

from __future__ import annotations

from paper_library.time_utils import ensure_utc  # SQLite returns naive UTC, PostgreSQL aware
import contextlib
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest


def _pdf_bytes(pages: int = 1) -> bytes:
    document = pymupdf.open()
    try:
        for _ in range(pages):
            document.new_page()
        return document.tobytes()
    finally:
        document.close()


def test_queue_rejects_invalid_attempts_and_preserves_delayed_jobs(monkeypatch):
    from worker.queue import enqueue, enqueue_exclusive, get_job

    monkeypatch.setenv("JOBS_INLINE", "true")
    with pytest.raises(ValueError, match="at least one"):
        enqueue("invalid", {}, max_attempts=0)
    with pytest.raises(ValueError, match="at least one"):
        enqueue_exclusive("invalid", {}, max_attempts=0)

    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    job_id = enqueue("delayed", {"n": 1}, run_after=future, dedupe_key="delay-key")
    assert get_job(job_id)["status"] == "queued"
    assert get_job(job_id)["run_after"].replace(tzinfo=dt.UTC) >= future.replace(microsecond=0)


def test_queue_progress_owner_checks_and_default_backoff(monkeypatch):
    from paper_library.db import get_session
    from paper_library.models import Job
    from worker.queue import (
        claim_next,
        complete,
        enqueue,
        fail,
        get_job,
        heartbeat,
        mark_succeeded,
        update_result,
    )

    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("progress", {}, max_attempts=3)
    assert claim_next("owner", ["progress"])["id"] == job_id
    assert heartbeat(job_id, "wrong-owner") is False
    assert update_result(job_id, {"step": 1}, worker_id="wrong-owner") is False
    assert heartbeat(job_id, "owner") is True
    assert update_result(job_id, {"step": 1}, worker_id="owner") is True
    assert get_job(job_id)["result"] == {"step": 1}
    assert fail(job_id, "retry me", worker_id="owner") is True
    retry = get_job(job_id)
    assert retry["status"] == "queued"
    assert ensure_utc(retry["run_after"]) > dt.datetime.now(dt.UTC)

    with get_session() as session:
        session.get(Job, job_id).run_after = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)

    assert claim_next("owner-2", ["progress"])["id"] == job_id
    assert complete(job_id, "scalar result", worker_id="owner-2") is True
    assert get_job(job_id)["result"] == {"value": "scalar result"}
    assert mark_succeeded("missing", {}) is False


def test_queue_dedupe_terminal_requeue_and_cancel_helpers(monkeypatch):
    from worker.queue import (
        cancel_by_dedupe_key,
        enqueue,
        find_by_dedupe_key,
        find_jobs,
        get_job,
        mark_succeeded,
    )

    monkeypatch.setenv("JOBS_INLINE", "false")
    job_id = enqueue("repeat", {"old": True}, dedupe_key="repeat-key")
    assert mark_succeeded(job_id, {"ok": True}) is True
    assert enqueue("repeat", {"new": True}, dedupe_key="repeat-key") == job_id
    reset_id = enqueue(
        "repeat-again", {"new": True}, dedupe_key="repeat-key", requeue_terminal=True,
        max_attempts=4, priority=8,
    )
    assert reset_id == job_id
    saved = get_job(job_id)
    assert saved["status"] == "queued"
    assert saved["kind"] == "repeat-again"
    assert saved["payload"] == {"new": True}
    assert saved["attempts"] == 0
    assert saved["max_attempts"] == 4
    assert saved["priority"] == 8
    assert find_by_dedupe_key("repeat-key")["id"] == job_id
    assert [row["id"] for row in find_jobs(kind="repeat-again", statuses=("queued",))] == [job_id]
    assert cancel_by_dedupe_key("no-such-key") is False
    assert cancel_by_dedupe_key("repeat-key") is True


def test_worker_registry_dispatch_and_unknown_kind(monkeypatch):
    from api.routers import literature_search, papers, videos
    from api import scheduler as api_scheduler
    from api import summary_jobs
    from worker import registry

    monkeypatch.setattr(videos, "execute_video_publish", lambda payload: {"status": "success", **payload})
    assert registry._publish_video({"record_id": 3}, "job") == {"status": "success", "record_id": 3}
    monkeypatch.setattr(videos, "execute_video_publish", lambda _payload: {"status": "failed"})
    with pytest.raises(registry.PermanentJobError):
        registry._publish_video({"record_id": 4}, "job")
    with pytest.raises(registry.PermanentJobError, match="missing record_id"):
        registry._publish_video({}, "job")

    monkeypatch.setattr(literature_search, "run_literature_search", lambda job_id, payload: (job_id, payload))
    assert registry._literature_search({"request": {"days": 60}}, "literature-1") == (
        "literature-1", {"request": {"days": 60}}
    )
    with pytest.raises(registry.PermanentJobError):
        registry._literature_search({"request": {"days": 1096}}, "literature-2")
    monkeypatch.setattr(papers, "poll_generated_video_download", lambda _payload: {"status": "timeout"})
    with pytest.raises(registry.PermanentJobError):
        registry._poll_download({"task_id": "t-1", "video_id": 1}, "job")
    monkeypatch.setattr(summary_jobs, "run_summary_job", lambda job_id: {"id": job_id})
    assert registry._summary_job({"summary_job_id": 7}, "job") == {"id": "7"}
    monkeypatch.setattr(api_scheduler, "_run_cookie_validation", lambda ws, cfg: (ws, cfg))
    assert registry._cookie_validation({"workspace": "/tmp/ws", "config_dir": "/tmp/cfg"}, "job") == (
        "/tmp/ws", "/tmp/cfg"
    )

    monkeypatch.setitem(registry.HANDLERS, "temporary", lambda payload, _job: payload)
    registry.register("temporary", lambda payload, _job: {"registered": payload})
    assert registry.get_handler("temporary")({"v": 1}, "job") == {"registered": {"v": 1}}
    with pytest.raises(LookupError, match="registered"):
        registry.get_handler("not-a-handler")


def test_publish_handler_missing_and_terminal_records(monkeypatch):
    from api.config import settings
    from api.services import publishing
    from worker.registry import PermanentJobError, _publish_draft

    class FakeSession:
        def __init__(self, record):
            self.record = record

        def get(self, _model, _record_id):
            return self.record

    @contextlib.contextmanager
    def fake_session(*_args, **_kwargs):
        yield FakeSession(record)

    record = None
    from paper_library import db
    monkeypatch.setattr(db, "get_session", fake_session)
    assert _publish_draft({"record_id": "15"}, "job") == {"record_id": 15, "status": "missing"}

    record = SimpleNamespace(id=16, status="success", error=None, url="file:///published.html")
    assert _publish_draft({"record_id": 16}, "job")["status"] == "success"
    record.status = "failed"
    record.error = "publisher error"
    with pytest.raises(PermanentJobError, match="publisher error"):
        _publish_draft({"record_id": 16}, "job")

    record.status = "pending"
    calls = []
    monkeypatch.setattr(
        publishing,
        "execute_publish_record",
        lambda *args: calls.append(args) or {"status": "failed"},
    )
    with pytest.raises(PermanentJobError):
        _publish_draft({"record_id": 16, "workspace": settings.workspace}, "job")
    assert calls == [(16, settings.workspace, settings.config_dir)]


def test_scheduler_reclaims_stuck_publish_records_from_jobs(monkeypatch):
    from paper_library.db import get_session
    from paper_library.models import ArticleDraft, Job, PublishRecord
    from worker.scheduler import _reconcile_pending_publishes

    from worker import scheduler

    monkeypatch.setenv("JOBS_INLINE", "false")
    monkeypatch.setattr(scheduler, "PUBLISH_STUCK_MINUTES", 1)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Scheduler", content="body")
        session.add(draft)
        session.flush()
        records = [
            PublishRecord(draft_id=draft.id, status="publishing", created_at=old, updated_at=old),
            PublishRecord(draft_id=draft.id, status="publishing", created_at=old, updated_at=old),
            PublishRecord(draft_id=draft.id, status="publishing", created_at=old, updated_at=old),
            PublishRecord(draft_id=draft.id, status="publishing", created_at=old, updated_at=old),
        ]
        session.add_all(records)
        session.flush()
        session.add_all([
            Job(
                id=f"stuck-{records[1].id}", kind="publish_draft", payload={}, status="succeeded",
                priority=0, run_after=old, attempts=1, max_attempts=3, created_at=old, updated_at=old,
                dedupe_key=f"publish-draft-record:{records[1].id}", result={"status": "success", "url": "file:///ok"},
            ),
            Job(
                id=f"failed-{records[2].id}", kind="publish_draft", payload={}, status="failed",
                priority=0, run_after=old, attempts=1, max_attempts=1, created_at=old, updated_at=old,
                dedupe_key=f"publish-draft-record:{records[2].id}", error="worker failed",
            ),
            Job(
                id=f"active-{records[3].id}", kind="publish_draft", payload={}, status="running",
                priority=0, run_after=old, attempts=1, max_attempts=3, created_at=old, updated_at=old,
                dedupe_key=f"publish-draft-record:{records[3].id}",
            ),
        ])
        record_ids = [record.id for record in records]

    assert _reconcile_pending_publishes(".", "config") == 3
    with get_session() as session:
        states = [session.get(PublishRecord, record_id) for record_id in record_ids]
        assert states[0].status == "failed"
        assert "对应任务不存在" in states[0].error
        assert states[1].status == "success"
        assert states[1].url == "file:///ok"
        assert states[2].status == "failed"
        assert states[2].error == "worker failed"
        assert states[3].status == "publishing"


def test_scheduler_queues_due_schedules_and_cookie_validation(monkeypatch):
    from paper_library.db import get_session
    from paper_library.models import ArticleDraft, Job, PublishRecord
    from worker.scheduler import _queue_cookie_validation, _queue_scheduled_publishes

    from worker.queue import find_jobs

    monkeypatch.setenv("JOBS_INLINE", "false")
    now = dt.datetime.now(dt.UTC)
    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Due schedule", content="body")
        session.add(draft)
        session.flush()
        due = PublishRecord(
            draft_id=draft.id, status="scheduled", scheduled_at=now - dt.timedelta(minutes=1)
        )
        later = PublishRecord(
            draft_id=draft.id, status="scheduled", scheduled_at=now + dt.timedelta(days=1)
        )
        session.add_all([due, later])
        session.flush()
        due_id, later_id = due.id, later.id

    assert _queue_scheduled_publishes("/workspace", "/config") == 1
    assert _queue_scheduled_publishes("/workspace", "/config") == 0
    with get_session() as session:
        assert session.get(PublishRecord, due_id).status == "pending"
        assert session.get(PublishRecord, later_id).status == "scheduled"
    queued = find_jobs(kind="publish_draft", statuses=("queued",))
    assert len(queued) == 1
    assert queued[0]["payload"]["workspace"] == "/workspace"

    _queue_cookie_validation("/workspace", "/config")
    first = find_jobs(kind="cookie_validation")
    assert len(first) == 1
    assert first[0]["dedupe_key"] == "cookie-validation:first"
    assert ensure_utc(first[0]["run_after"]) > dt.datetime.now(dt.UTC)

    with get_session() as session:
        job = session.get(Job, first[0]["id"])
        job.status = "succeeded"
        job.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=22)
    _queue_cookie_validation("/workspace", "/config")
    all_validation = find_jobs(kind="cookie_validation")
    assert len(all_validation) == 2
    assert all_validation[0]["dedupe_key"] != "cookie-validation:first"


def test_scheduler_lease_can_be_taken_over_after_expiration():
    from paper_library.db import get_session
    from paper_library.models import SchedulerLease
    from worker.scheduler import acquire_lease

    assert acquire_lease("first", lease_seconds=1) is True
    with get_session() as session:
        lease = session.get(SchedulerLease, "main")
        lease.locked_until = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    assert acquire_lease("second", lease_seconds=20) is True
    with get_session() as session:
        assert session.get(SchedulerLease, "main").locked_by == "second"


def test_scheduler_tick_invokes_maintenance_and_run_scheduler_honors_pre_set_stop(monkeypatch):
    from api import scheduler as api_scheduler
    from paper_library import governance
    from worker import scheduler

    calls = []
    monkeypatch.setattr(governance, "release_stale_reservations", lambda: calls.append("release") or 0)
    monkeypatch.setattr(governance, "expire_access_requests", lambda: calls.append("expire") or 0)
    monkeypatch.setattr(api_scheduler, "reset_stale_paper_states", lambda *_: calls.append("reset"))
    monkeypatch.setattr(scheduler, "_queue_scheduled_publishes", lambda *_: calls.append("scheduled") or 0)
    monkeypatch.setattr(scheduler, "_reconcile_pending_publishes", lambda *_: calls.append("reconcile") or 0)
    monkeypatch.setattr(scheduler, "_queue_cookie_validation", lambda *_: calls.append("cookie"))
    monkeypatch.setattr(scheduler, "_queue_pdf_flag_reconciliation", lambda *_: calls.append("pdf_flags"))
    monkeypatch.setenv("JOBS_INLINE", "false")
    scheduler.scheduler_tick("/workspace", "/config")
    assert calls == ["release", "expire", "reset", "scheduled", "reconcile", "cookie", "pdf_flags"]

    stopped = __import__("threading").Event()
    stopped.set()
    scheduler.run_scheduler(stopped)


def test_pdf_extractor_reads_embedded_images_and_respects_missing_inputs(tmp_path, monkeypatch):
    from PIL import Image
    from core.pdf_extractor import extract_pdf_content, extract_text_and_images

    png_path = tmp_path / "input.png"
    Image.new("RGB", (12, 8), color=(20, 40, 60)).save(png_path)
    pdf_path = tmp_path / "with-image.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Figure 1: A synthetic image caption")
    page.insert_image(pymupdf.Rect(72, 100, 180, 170), filename=str(png_path))
    doc.save(pdf_path)
    doc.close()

    monkeypatch.chdir(tmp_path)
    text, images = extract_text_and_images(str(pdf_path), tmp_path / "extracted")
    assert "synthetic image caption" in text
    assert len(images) == 1
    assert images[0]["page"] == 1
    assert images[0]["caption"].startswith("Figure 1")
    assert Path(images[0]["path"]).is_file()
    assert extract_text_and_images(None, tmp_path / "none") == ("", [])
    assert extract_text_and_images(str(tmp_path / "missing.pdf"), tmp_path / "missing") == ("", [])
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a PDF")
    with pytest.raises(pymupdf.FileDataError):
        extract_text_and_images(str(corrupt), tmp_path / "corrupt")
    assert extract_pdf_content(None, tmp_path / "no-input") == ("", [], "none")
    assert extract_pdf_content(str(tmp_path / "absent.pdf"), tmp_path / "absent") == ("", [], "missing")


def test_mineru_cached_output_filters_small_images_and_falls_back(tmp_path, monkeypatch):
    from core.pdf_extractor import extract_pdf_content

    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(_pdf_bytes(2))
    mineru_dir = tmp_path / "mineru"
    images_dir = mineru_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "large.png").write_bytes(b"x" * 9000)
    (images_dir / "small.png").write_bytes(b"x" * 20)
    (mineru_dir / "result.md").write_text(
        "Extracted text\n\n![large](images/large.png)\n\n![small](images/small.png)",
        encoding="utf-8",
    )

    text, images, method = extract_pdf_content(
        str(pdf_path), tmp_path / "images",
        extractor_cfg={"default": "mineru", "mineru": {"method": "txt", "min_image_bytes": 100}},
    )
    assert "Extracted text" in text
    assert method == "mineru"
    assert len(images) == 1
    assert images[0].endswith("large.png")

    text, images, method = extract_pdf_content(
        str(pdf_path), tmp_path / "fallback-images",
        extractor_cfg={
            "default": "mineru",
            "fallback_to_pymupdf": True,
            "mineru": {"method": "unsupported"},
        },
    )
    assert method == "pymupdf_fallback"
    assert text == ""
    assert images == []

    text, images, reason = extract_pdf_content(
        str(pdf_path), tmp_path / "no-fallback",
        extractor_cfg={
            "default": "mineru",
            "fallback_to_pymupdf": False,
            "mineru": {"method": "unsupported"},
        },
    )
    assert (text, images, reason) == ("", [], "mineru_failed:mineru_invalid_method:unsupported")


def test_mineru_subprocess_success_and_remote_configuration_error(tmp_path, monkeypatch):
    from core import pdf_extractor

    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(_pdf_bytes())
    image_dir = tmp_path / "images"

    class FakeProcess:
        returncode = 0
        pid = 4321

        def __init__(self, command, **_kwargs):
            output_dir = Path(command[command.index("-o") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "generated.md").write_text("MinerU extracted content", encoding="utf-8")

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(pdf_extractor.subprocess, "Popen", FakeProcess)
    text, images, reason = pdf_extractor._extract_with_mineru(
        pdf_path, image_dir, 3, {"method": "txt", "timeout_seconds": 10}
    )
    assert text == "MinerU extracted content"
    assert images == []
    assert reason == "ok"

    text, images, reason = pdf_extractor._extract_with_mineru(
        pdf_path, tmp_path / "remote-images", 3,
        {"mode": "remote", "method": "auto", "base_url": "", "api_token": ""},
    )
    assert (text, images, reason) == ("", [], "mineru_remote_not_configured")


def test_publisher_registry_builds_account_variants():
    from publisher.registry import build_publisher_from_account

    assert build_publisher_from_account({"channel": "unknown"}) is None
    csdn = build_publisher_from_account({"channel": "csdn", "credential": "  cookie  "})
    assert csdn is not None
    assert csdn.cookie == "cookie"


def test_credential_encryption_invalid_key_boundaries(monkeypatch):
    from cryptography.fernet import Fernet
    from publisher import credential_store

    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "invalid-fernet-key")
    with pytest.raises(ValueError, match="urlsafe base64 Fernet key"):
        credential_store.encrypt_credential("synthetic credential")
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    assert credential_store.encrypt_credential(None) is None


def test_credential_warning_is_emitted_once_without_a_key(monkeypatch, caplog):
    from publisher import credential_store

    monkeypatch.delenv("CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_PLAINTEXT_CREDENTIALS", raising=False)
    monkeypatch.setattr(credential_store, "_warned_without_key", False)
    with caplog.at_level("WARNING", logger="publisher.credential_store"):
        credential_store.warn_if_encryption_key_missing()
        credential_store.warn_if_encryption_key_missing()
    matching = [record for record in caplog.records if "CREDENTIALS_ENCRYPTION_KEY is not set" in record.message]
    assert len(matching) == 1
