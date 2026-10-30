"""Fast unit tests for draft helpers and direct route behavior.

The complementary TestClient contract tests live in test_drafts_api_c40.py and
are run by the external full suite.
"""

from __future__ import annotations

import datetime as dt
import queue
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from api.routers import drafts
from api.services import draft_images, drafts as draft_service, publishing, publisher_runtime
from paper_library.db import get_session
from paper_library.models import ArticleDraft, ChannelAccount, Paper, PublishRecord, draft_papers


def _draft(title="Synthetic draft", *, status="draft", draft_type="review", meta=None):
    return ArticleDraft(
        draft_type=draft_type,
        title=title,
        content="Synthetic body",
        status=status,
        meta=meta,
    )


def test_draft_dict_and_listing_search_cover_paper_and_manual_image_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.settings, "workspace", str(tmp_path))
    image = tmp_path / "data" / "images" / "paper" / "figure.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"x" * 2048)
    manual_image = tmp_path / "data" / "images" / "draft_900" / "manual.png"
    manual_image.parent.mkdir(parents=True)
    manual_image.write_bytes(b"manual image bytes")

    with get_session() as session:
        paper = Paper(
            doi="10.5555/synthetic",
            title="Linked synthetic paper",
            title_zh="关联论文",
            internal_id=731,
            abstract="Synthetic abstract",
            conclusions_zh="Synthetic conclusion",
            selected_images=["data/images/paper/figure.png"],
            extracted_images=[{"path": "data/images/paper/figure.png", "caption": "Figure caption"}],
        )
        linked = _draft("Linked searchable draft")
        manual = _draft(
            "Manual searchable draft",
            draft_type="manual_upload",
            meta={"images": ["data/images/draft_900/manual.png"]},
        )
        session.add_all([paper, linked, manual])
        session.flush()
        session.execute(draft_papers.insert().values(draft_id=linked.id, paper_id=paper.id))
        paper_id, linked_id, manual_id = paper.id, linked.id, manual.id

        result = draft_service.draft_to_dict(linked, session, include_paper=True, workspace=str(tmp_path))
        assert result["paper_title"] == "Linked synthetic paper"
        assert result["paper_abstract"] == "Synthetic abstract"
        assert result["paper_conclusions_zh"] == "Synthetic conclusion"
        assert result["paper_selected_images"] == ["data/images/paper/figure.png"]
        assert result["paper_images"][0]["size_kb"] > 0

    numeric_paper_search = drafts.list_drafts(
        status=["draft"], q=str(paper_id), page=1, page_size=10, user={}
    )
    assert [item["id"] for item in numeric_paper_search["items"]] == [linked_id]
    numeric_internal_search = drafts.list_drafts(
        status=[], q="731", page=1, page_size=10, user={}
    )
    assert linked_id in [item["id"] for item in numeric_internal_search["items"]]
    text_search = drafts.list_drafts(status=[], q="manual searchable", page=1, page_size=1, user={})
    assert text_search["total"] == 1
    assert text_search["items"][0]["id"] == manual_id

    with get_session() as session:
        saved_manual = session.get(ArticleDraft, manual_id)
        full_manual = draft_service.draft_to_dict(saved_manual, session, include_paper=True, workspace=str(tmp_path))
        assert full_manual["paper_images"][0]["path"].endswith("manual.png")


def test_draft_patch_delete_and_outbox_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    monkeypatch.setattr(drafts.settings, "workspace", str(tmp_path))
    with get_session() as session:
        paper = Paper(title="Paper to unlink", article_status="generated")
        item = _draft("Review draft", meta={"keep": 1})
        other = _draft("Other draft")
        session.add_all([paper, item, other])
        session.flush()
        session.execute(draft_papers.insert().values(draft_id=item.id, paper_id=paper.id))
        session.execute(draft_papers.insert().values(draft_id=other.id, paper_id=paper.id))
        records = [
            PublishRecord(draft_id=item.id, channel="csdn", account_name="Synthetic account", status="pending"),
            PublishRecord(draft_id=item.id, channel="csdn", account_name="Synthetic account", status="scheduled"),
            PublishRecord(draft_id=item.id, channel="csdn", account_name="Synthetic account", status="success"),
            PublishRecord(draft_id=other.id, channel="csdn", account_name="Synthetic account", status="failed"),
        ]
        session.add_all(records)
        session.flush()
        record_ids = [record.id for record in records]
        draft_id, other_id, paper_id = item.id, other.id, paper.id

    from worker.queue import enqueue

    job_id = enqueue(
        "publish_draft", {"record_id": record_ids[0]},
        dedupe_key=f"publish-draft-record:{record_ids[0]}", inline=False,
    )
    patched = drafts.patch_draft(
        draft_id,
        drafts.PatchDraftRequest(
            title="Updated title", content="Updated body", status="approved",
            review_notes="Reviewed", meta={"new": 2}, selected_images=["new-image.png"],
        ),
        user={"username": "reviewer"},
    )
    assert patched["ok"] is True
    with get_session() as session:
        saved = session.get(ArticleDraft, draft_id)
        assert saved.title == "Updated title"
        meta_merge_persisted = saved.meta == {"keep": 1, "new": 2}
        assert saved.reviewer == "reviewer"
        assert session.get(Paper, paper_id).selected_images == ["new-image.png"]

    outbox = drafts.list_outbox(user={})
    assert len(outbox["items"]) == 2
    assert any(account_row["job_id"] == job_id for group in outbox["items"] for account_row in group["accounts"])
    cancelled = publishing.cancel_publish_records_for_draft(
        draft_id, workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir
    )
    assert cancelled == 2
    with get_session() as session:
        assert session.get(PublishRecord, record_ids[0]).status == "cancelled"
        assert session.get(PublishRecord, record_ids[1]).status == "cancelled"
    assert drafts.clear_outbox(user={}) == {"ok": True, "hidden": 4}
    with pytest.raises(HTTPException) as bad_status:
        drafts.cancel_outbox_record(record_ids[2], user={})
    assert bad_status.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        drafts.cancel_outbox_record(999999, user={})
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as missing_draft:
        drafts.get_draft(999999, user={})
    assert missing_draft.value.status_code == 404
    assert drafts.delete_draft(draft_id, user={}) == {"ok": True}
    with get_session() as session:
        assert session.get(ArticleDraft, draft_id) is None
        assert session.get(Paper, paper_id).article_status == "generated"
        assert session.get(ArticleDraft, other_id) is not None

    assert meta_merge_persisted


def test_publish_routes_and_schedule_helpers_directly(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    with get_session() as session:
        first = _draft("Publish one")
        second = _draft("Publish two")
        account = ChannelAccount(channel="csdn", account_name="Synthetic account", credential_type="cookie")
        session.add_all([first, second, account])
        session.flush()
        first_id, second_id, account_id = first.id, second.id, account.id

    published = publishing.enqueue_publish(
        [first_id], [account_id], idempotency_key="direct-idem",
        workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
    )
    repeated = publishing.enqueue_publish(
        [first_id], [account_id], idempotency_key="direct-idem",
        workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
    )
    assert published[0]["record_id"] == repeated[0]["record_id"]
    batch_published = publishing.enqueue_publish(
        [first_id, second_id], [account_id], idempotency_key="direct-batch-publish",
        workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
    )
    assert len(batch_published) == 2
    with get_session() as session:
        batch_records = session.query(PublishRecord).filter(
            PublishRecord.idempotency_key == "direct-batch-publish",
        ).all()
        assert len(batch_records) == 2
        assert all(record.channel == "csdn" and record.account_id == account_id for record in batch_records)
        assert all(record.account_name == "Synthetic account" for record in batch_records)
    with pytest.raises(publishing.MissingDrafts):
        publishing.enqueue_publish(
            [second_id, 999999], [account_id], idempotency_key="direct-batch",
            workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
        )
    assert publishing.parse_scheduled_datetime("2030-04-05T10:30").utcoffset() == dt.timedelta(0)
    assert publishing.parse_scheduled_datetime("2030-04-05T10:30+08:00").hour == 2

    scheduled = publishing.schedule_records(
        [second_id], [account_id], publishing.parse_scheduled_datetime("2030-04-05T10:30"),
        workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
    )
    assert scheduled["account_count"] == 1
    monkeypatch.setattr(publishing.random, "uniform", lambda low, high: 0)
    batched = publishing.schedule_records(
        [first_id, second_id], [account_id], publishing.parse_scheduled_datetime("2030-04-06T09:00"),
        interval_hours=6, jitter_hours=0,
        workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir,
    )
    assert batched["scheduled_records"] == 2
    with get_session() as session:
        record_id = session.query(PublishRecord).filter_by(draft_id=second_id, status="scheduled").first().id
    publishing.cancel_publish_record(record_id, workspace=drafts.settings.workspace, config_dir=drafts.settings.config_dir)


def test_publish_services_reject_partial_invalid_account_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.settings, "workspace", str(tmp_path))
    with get_session() as session:
        draft = _draft("Target validation")
        active = ChannelAccount(channel="csdn", account_name="Active", credential_type="cookie", is_active=True)
        inactive = ChannelAccount(channel="csdn", account_name="Inactive", credential_type="cookie", is_active=False)
        session.add_all([draft, active, inactive])
        session.flush()
        draft_id, active_id, inactive_id = draft.id, active.id, inactive.id

    options = {"workspace": str(tmp_path), "config_dir": drafts.settings.config_dir}
    with pytest.raises(publishing.InvalidPublishTargets) as invalid:
        publishing.resolve_publish_targets([active_id, inactive_id, 999999], **options)
    assert invalid.value.invalid_ids == sorted([inactive_id, 999999])
    with pytest.raises(publishing.InvalidPublishTargets):
        publishing.enqueue_publish(
            [draft_id], [active_id, 999999], idempotency_key=None, **options
        )
    with pytest.raises(publishing.InvalidPublishTargets):
        publishing.schedule_records(
            [draft_id], [inactive_id], publishing.parse_scheduled_datetime("2030-04-05T10:30"), **options
        )
    with get_session() as session:
        assert session.query(PublishRecord).filter_by(draft_id=draft_id).count() == 0


def test_cover_image_selection_and_image_doi_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    small = tmp_path / "data" / "images" / "small.png"
    large = tmp_path / "data" / "images" / "large.png"
    small.parent.mkdir(parents=True)
    Image.new("RGB", (120, 80), "white").save(small)
    Image.new("RGB", (420, 300), "white").save(large)

    paper = Paper(
        doi="10.5555/image-map",
        extracted_images=[
            {"path": "data/images/small.png"},
            {"file_path": "data/images/large.png"},
        ],
        selected_images=[],
    )
    assert draft_images.pick_cover_image(paper, tmp_path) == "data/images/large.png"
    assert draft_images.collect_cover_images(paper, tmp_path) == ["data/images/large.png", "data/images/small.png"]

    item = _draft("Image mapping")
    item.id = 55
    paper.selected_images = ["selected\\image.png"]
    paper.extracted_images.append({"path": "extracted/image.png"})
    monkeypatch.setattr(draft_images, "get_papers_for_draft", lambda *_args: [paper])
    mapping = draft_images.build_image_doi_map(item, object(), tmp_path)
    assert mapping == {
        "selected/image.png": "10.5555/image-map",
        "data/images/small.png": "10.5555/image-map",
        "data/images/large.png": "10.5555/image-map",
        "extracted/image.png": "10.5555/image-map",
    }


def test_thumbnail_and_image_serving_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts.settings, "workspace", str(tmp_path))
    source = tmp_path / "image.png"
    Image.new("RGBA", (64, 32), (255, 0, 0, 100)).save(source)
    first = draft_images.get_image_thumbnail(source, tmp_path, 32)
    second = draft_images.get_image_thumbnail(source, tmp_path, 32)
    assert first == second
    assert first.suffix == ".jpg"
    assert Image.open(first).size == (32, 16)

    served = drafts.serve_image("image.png", thumbnail=False, size=512, user=None)
    assert served.path == str(source)
    with pytest.raises(HTTPException) as traversal:
        drafts.serve_image("../outside.png", thumbnail=False, size=512, user=None)
    assert traversal.value.status_code == 400
    with pytest.raises(HTTPException) as missing:
        drafts.serve_image("missing.png", thumbnail=False, size=512, user=None)
    assert missing.value.status_code == 404


def test_publisher_process_entry_and_isolated_process_outcomes(monkeypatch):
    from publisher.base import PublishResult

    class ResultQueue:
        def __init__(self, result=None, error=None):
            self.result = result
            self.error = error
            self.closed = False
            self.joined = False

        def put(self, value):
            self.result = value

        def get(self, timeout=None):
            if self.error:
                raise self.error
            return self.result

        def close(self):
            self.closed = True

        def join_thread(self):
            self.joined = True

    result_queue = ResultQueue()
    monkeypatch.setattr(publisher_runtime.os, "setsid", lambda: None, raising=False)
    monkeypatch.setattr(
        "publisher.registry.build_publisher_from_account",
        lambda *_args, **_kwargs: SimpleNamespace(
            publish=lambda **_kwargs: PublishResult(success=True, channel="csdn", url="https://example.test/synthetic")
        ),
    )
    publisher_runtime.publisher_process_entry(result_queue, {"channel": "csdn", "id": 1}, 7, "title", "body", [], {})
    assert result_queue.result["success"] is True

    missing_queue = ResultQueue()
    monkeypatch.setattr("publisher.registry.build_publisher_from_account", lambda *_args, **_kwargs: None)
    publisher_runtime.publisher_process_entry(missing_queue, {"channel": "unknown"}, 8, "title", "body", [], {})
    assert missing_queue.result["error"] == "Publisher not implemented for this channel"

    class FakeProcess:
        exitcode = 0

        def __init__(self, *, alive=False, start_error=None):
            self.alive = alive
            self.start_error = start_error

        def start(self):
            if self.start_error:
                raise self.start_error

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            return None

    class FakeContext:
        def __init__(self, result_queue, process):
            self.result_queue = result_queue
            self.process = process

        def Queue(self, maxsize=1):
            return self.result_queue

        def Process(self, **_kwargs):
            return self.process

    completed_queue = ResultQueue({"success": True, "channel": "csdn"})
    monkeypatch.setattr(publisher_runtime.multiprocessing, "get_context", lambda _name: FakeContext(completed_queue, FakeProcess()))
    result = publisher_runtime.run_publisher_isolated({"channel": "csdn"}, 1, "t", "c", [], {}, lambda: False)
    assert result["success"] is True
    assert completed_queue.closed is True
    assert completed_queue.joined is True

    empty_queue = ResultQueue(error=queue.Empty)
    monkeypatch.setattr(publisher_runtime.multiprocessing, "get_context", lambda _name: FakeContext(empty_queue, FakeProcess()))
    empty_result = publisher_runtime.run_publisher_isolated({"channel": "csdn"}, 2, "t", "c", [], {}, lambda: False)
    assert "exit code: 0" in empty_result["error"]


def test_isolated_publisher_cancel_timeout_and_start_failure(monkeypatch):
    class NoopQueue:
        def get(self, timeout=None):
            return {"success": True}

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = None

        def __init__(self, *, alive=True, start_error=None):
            self.alive = alive
            self.start_error = start_error

        def start(self):
            if self.start_error:
                raise self.start_error

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            pass

    class Context:
        def __init__(self, process):
            self.process = process

        def Queue(self, maxsize=1):
            return NoopQueue()

        def Process(self, **_kwargs):
            return self.process

    monkeypatch.setattr(publisher_runtime, "terminate_publisher_process", lambda _process: None)
    monkeypatch.setattr(publisher_runtime.multiprocessing, "get_context", lambda _name: Context(FakeProcess()))
    cancelled = publisher_runtime.run_publisher_isolated({"channel": "csdn"}, 1, "t", "c", [], {}, lambda: True)
    assert cancelled["error"] == "已取消"

    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(publisher_runtime.time, "monotonic", lambda: next(ticks))
    timed_out = publisher_runtime.run_publisher_isolated(
        {"channel": "csdn"}, 1, "t", "c", [], {}, lambda: False, timeout_seconds=1
    )
    assert "发布超时" in timed_out["error"]

    process = FakeProcess(start_error=RuntimeError("synthetic spawn error"))
    monkeypatch.setattr(publisher_runtime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(publisher_runtime.multiprocessing, "get_context", lambda _name: Context(process))
    failed = publisher_runtime.run_publisher_isolated({"channel": "csdn"}, 1, "t", "c", [], {}, lambda: False)
    assert "无法启动发布进程" in failed["error"]


def test_publisher_process_termination_branches(monkeypatch):
    class FakeProcess:
        pid = 1234

        def __init__(self, alive):
            self.alive = alive
            self.calls = []

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            self.calls.append(("join", timeout))

        def terminate(self):
            self.calls.append(("terminate",))

        def kill(self):
            self.calls.append(("kill",))

    stopped = FakeProcess(False)
    publisher_runtime.terminate_publisher_process(stopped)
    assert stopped.calls == [("join", 1)]

    alive = FakeProcess(True)
    monkeypatch.setattr(publisher_runtime.os, "killpg", lambda *_args: (_ for _ in ()).throw(ProcessLookupError()))
    publisher_runtime.terminate_publisher_process(alive)
    assert ("terminate",) in alive.calls


def test_execute_publish_record_updates_status_with_stubbed_publisher(tmp_path, monkeypatch):
    with get_session() as session:
        draft = _draft("Execute publish")
        account = ChannelAccount(channel="csdn", account_name="Synthetic account", credential_type="cookie")
        session.add_all([draft, account])
        session.flush()
        record = PublishRecord(
            draft_id=draft.id, account_id=account.id, channel="csdn", account_name="Synthetic account", status="pending"
        )
        session.add(record)
        session.flush()
        record_id, draft_id = record.id, draft.id

    monkeypatch.setattr(publishing, "run_publisher_isolated", lambda **_kwargs: {
        "success": True, "url": "https://example.test/synthetic", "error": None,
    })
    result = publishing.execute_publish_record(record_id, drafts.settings.workspace, drafts.settings.config_dir)
    assert result["status"] == "success"
    with get_session() as session:
        assert session.get(PublishRecord, record_id).status == "success"
        assert session.get(ArticleDraft, draft_id).status == "published"
