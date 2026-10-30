"""TestClient regressions for review, regeneration, publishing, and filters."""

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.dependencies import require_reviewer
from api.main import app
from api.routers import papers as papers_router
from api.routers.papers import detail as papers_detail, generation as papers_generation
from api.routers import videos as videos_router
from core import entity_detector
from paper_library.db import get_session
from paper_library.models import ArticleDraft, Base, ChannelAccount, Job, Paper, PublishRecord, draft_papers


def _factory_session_override(factory):
    @contextmanager
    def session_context(*_args, **_kwargs):
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return session_context


def _install_generation_stubs(monkeypatch, workspace, *, chat):
    monkeypatch.setattr(papers_generation.settings, "workspace", str(workspace))
    monkeypatch.setattr(
        papers_generation.ConfigLoader,
        "load_all",
        lambda _self: {
            "prompts": {"system_prompt": "Test prompt"},
            "models": {"models": [{"id": "test-model"}]},
            "runtime": {"provider": {"base_url": "http://localhost", "api_key": "test"}},
            "domain": {
                "generation": {
                    "require_target_entity": bool(entity_detector.get_target_label()),
                },
            },
        },
    )
    monkeypatch.setattr(papers_generation, "build_messages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(papers_generation.OpenAICompatibleClient, "chat", chat)


def test_single_force_regen_llm_failure_keeps_old_draft_and_status(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'single-regeneration.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as session:
            paper = Paper(
                title="Regeneration paper",
                processing_status="done", review_outcome="approved",
                article_status="generated",
                entity_label=entity_detector.get_target_label(),
            )
            old = ArticleDraft(
                draft_type="single_daily", title="Old draft", content="Old content", status="draft"
            )
            session.add_all([paper, old])
            session.flush()
            session.execute(draft_papers.insert().values(draft_id=old.id, paper_id=paper.id))
            paper_id, old_id = paper.id, old.id
            session.commit()

        monkeypatch.setattr(papers_generation, "get_session", _factory_session_override(factory))

        def fail_chat(*_args, **_kwargs):
            raise RuntimeError("LLM unavailable")

        _install_generation_stubs(monkeypatch, tmp_path, chat=fail_chat)
        app.dependency_overrides[papers_router.get_current_user] = lambda: {
            "username": "test-user", "role": "admin",
        }
        with TestClient(app) as client:
            response = client.post(
                f"/api/papers/{paper_id}/generate-draft",
                json={"profile": "default", "model": "test-model", "force_regen": True},
            )

        assert response.status_code == 502
        with Session(engine) as session:
            assert session.get(Paper, paper_id).article_status == "generated"
            assert session.get(ArticleDraft, old_id) is not None
            assert session.scalar(select(draft_papers.c.draft_id).where(
                draft_papers.c.paper_id == paper_id
            )) == old_id
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        engine.dispose()


def test_single_force_regen_missing_credentials_keeps_old_draft(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-credentials.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as session:
            paper = Paper(
                title="Credential check",
                processing_status="done", review_outcome="approved",
                article_status="generated",
                entity_label=entity_detector.get_target_label(),
            )
            old = ArticleDraft(
                draft_type="single_daily", title="Old credential draft", content="Old content", status="draft"
            )
            session.add_all([paper, old])
            session.flush()
            session.execute(draft_papers.insert().values(draft_id=old.id, paper_id=paper.id))
            paper_id, old_id = paper.id, old.id
            session.commit()

        monkeypatch.setattr(papers_generation, "get_session", _factory_session_override(factory))
        monkeypatch.setattr(papers_generation.settings, "workspace", str(tmp_path))
        monkeypatch.setattr(
            papers_generation.ConfigLoader,
            "load_all",
            lambda _self: {
                "prompts": {},
                "models": {"models": [{"id": "test-model"}]},
                "runtime": {"provider": {"base_url": "http://localhost", "api_key": ""}},
                "domain": {
                    "generation": {
                        "require_target_entity": bool(entity_detector.get_target_label()),
                    },
                },
            },
        )
        app.dependency_overrides[papers_router.get_current_user] = lambda: {
            "username": "test-user", "role": "admin",
        }
        with TestClient(app) as client:
            response = client.post(
                f"/api/papers/{paper_id}/generate-draft",
                json={"profile": "default", "model": "test-model", "force_regen": True},
            )

        assert response.status_code == 503
        with Session(engine) as session:
            assert session.get(Paper, paper_id).article_status == "generated"
            assert session.get(ArticleDraft, old_id) is not None
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        engine.dispose()


def test_batch_force_regen_llm_failure_keeps_old_draft_and_status(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch-regeneration.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as session:
            paper = Paper(
                title="Batch regeneration paper",
                processing_status="done", review_outcome="approved",
                article_status="generated",
                entity_label=entity_detector.get_target_label(),
            )
            old = ArticleDraft(
                draft_type="single_daily", title="Old batch draft", content="Old content", status="draft"
            )
            session.add_all([paper, old])
            session.flush()
            session.execute(draft_papers.insert().values(draft_id=old.id, paper_id=paper.id))
            paper_id, old_id = paper.id, old.id
            session.commit()

        monkeypatch.setattr(papers_generation, "get_session", _factory_session_override(factory))

        def fail_chat(*_args, **_kwargs):
            raise RuntimeError("LLM unavailable")

        _install_generation_stubs(monkeypatch, tmp_path, chat=fail_chat)
        app.dependency_overrides[papers_router.get_current_user] = lambda: {
            "username": "test-user", "role": "admin",
        }
        with TestClient(app) as client:
            response = client.post(
                "/api/papers/batch-generate-draft",
                json={
                    "paper_ids": [paper_id],
                    "profile": "default",
                    "model": "test-model",
                    "force_regen": True,
                },
            )

        assert response.status_code == 200
        assert response.json() == {"success": 0, "failed": 1, "skipped": 0}
        with Session(engine) as session:
            assert session.get(Paper, paper_id).article_status == "generated"
            assert session.get(ArticleDraft, old_id) is not None
            assert session.scalar(select(draft_papers.c.draft_id).where(
                draft_papers.c.paper_id == paper_id
            )) == old_id
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        engine.dispose()


def test_review_permissions_and_approve_exclude_reset_decisions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as session:
            paper = Paper(
                title="Needs human metadata",
                processing_status="done",
                review_outcome="unreviewed",
            )
            pending = Paper(
                title="Still processing",
                processing_status="pending",
                review_outcome="unreviewed",
            )
            session.add_all([paper, pending])
            session.flush()
            paper_id, pending_id = paper.id, pending.id
            session.commit()

        monkeypatch.setattr(papers_detail, "get_session", _factory_session_override(factory))
        monkeypatch.setattr(papers_detail, "get_target_label", lambda: None)
        with TestClient(app) as client:
            for role in ("viewer", "limited"):
                app.dependency_overrides[papers_router.get_current_user] = lambda role=role: {
                    "username": "test-user", "role": role,
                }
                denied = client.post(
                    f"/api/papers/{paper_id}/review",
                    json={"decision": "approve"},
                )
                assert denied.status_code == 403

            app.dependency_overrides[papers_router.get_current_user] = lambda: {
                "username": "reviewer", "role": "reviewer",
            }
            for decision in ("approve", "exclude"):
                blocked = client.post(
                    f"/api/papers/{pending_id}/review",
                    json={"decision": decision},
                )
                assert blocked.status_code == 409
                assert "尚未处理完成" in blocked.json()["detail"]

            approved = client.post(
                f"/api/papers/{paper_id}/review",
                json={"decision": "approve", "note": "核对完成"},
            )
            assert approved.status_code == 200
            assert approved.json()["review_outcome"] == "approved"
            assert approved.json()["can_review"] is True
            assert approved.json()["reviewed_by"] == "reviewer"
            assert approved.json()["review_note"] == "核对完成"

            excluded = client.post(
                f"/api/papers/{paper_id}/review",
                json={"decision": "exclude", "note": "范围不符"},
            )
            assert excluded.status_code == 200
            assert excluded.json()["review_outcome"] == "excluded"
            assert excluded.json()["review_note"] == "范围不符"

            reset = client.post(
                f"/api/papers/{paper_id}/review",
                json={"decision": "reset"},
            )
            assert reset.status_code == 200
            assert reset.json()["review_outcome"] == "unreviewed"
            assert reset.json()["reviewed_by"] is None
            assert reset.json()["reviewed_at"] is None

        with Session(engine) as session:
            reviewed = session.get(Paper, paper_id)
            assert reviewed.processing_status == "done"
            assert reviewed.review_outcome == "unreviewed"
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        engine.dispose()


def test_generation_and_both_video_apis_reject_unusable_papers(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'generation-gates.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    target_label = "Target Entity"
    try:
        with factory() as session:
            paper = Paper(
                title="Lifecycle gate paper",
                internal_id=901,
                processing_status="done",
                review_outcome="unreviewed",
                entity_label=target_label,
            )
            session.add(paper)
            session.flush()
            paper_id = paper.id
            session.commit()

        session_override = _factory_session_override(factory)
        monkeypatch.setattr(papers_generation, "get_session", session_override)
        monkeypatch.setattr(papers_generation.settings, "workspace", str(tmp_path))
        monkeypatch.setattr(papers_generation, "get_target_label", lambda: target_label)
        monkeypatch.setattr(papers_generation, "generation_requires_target_entity", lambda _cfg: True)
        _install_generation_stubs(monkeypatch, tmp_path, chat=lambda *_args, **_kwargs: "unused")

        monkeypatch.setattr(videos_router, "get_session", session_override)
        monkeypatch.setattr(videos_router.settings, "workspace", str(tmp_path))
        monkeypatch.setattr(
            "core.domain_config.load_domain",
            lambda *_args, **_kwargs: {
                "generation": {"require_target_entity": True},
            },
        )
        monkeypatch.setattr(videos_router, "generation_requires_target_entity", lambda _cfg: True)
        monkeypatch.setattr(videos_router, "get_target_label", lambda: target_label)

        app.dependency_overrides[papers_router.get_current_user] = lambda: {
            "username": "admin", "role": "admin",
        }
        app.dependency_overrides[require_reviewer] = lambda: {
            "username": "admin", "role": "admin",
        }
        with TestClient(app) as client:
            single = client.post(
                f"/api/papers/{paper_id}/generate-draft",
                json={"profile": "default", "model": "test-model"},
            )
            assert single.status_code == 400

            batch = client.post(
                "/api/papers/batch-generate-draft",
                json={"paper_ids": [paper_id], "profile": "default", "model": "test-model"},
            )
            assert batch.status_code == 200
            assert batch.json() == {"success": 0, "failed": 0, "skipped": 1}

            paper_video = client.post(
                f"/api/papers/{paper_id}/generate-video",
                json={"resolution": "720P", "ratio": "16:9", "duration": 5},
            )
            assert paper_video.status_code == 400

            video_library = client.post("/api/videos/generate", json={"paper_id": 901})
            assert video_library.status_code == 400

        for processing_status, review_outcome, entity_label in (
            ("done", "excluded", target_label),
            ("failed", "approved", target_label),
            ("done", "approved", "Other Entity"),
        ):
            with factory() as session:
                paper = session.get(Paper, paper_id)
                paper.processing_status = processing_status
                paper.review_outcome = review_outcome
                paper.entity_label = entity_label
                session.commit()
            with TestClient(app) as client:
                response = client.post(
                    f"/api/papers/{paper_id}/generate-draft",
                    json={"profile": "default", "model": "test-model"},
                )
            assert response.status_code == 400
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        app.dependency_overrides.pop(require_reviewer, None)
        engine.dispose()


def test_publish_and_batch_publish_reuse_recent_idempotency_keys(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    with get_session() as session:
        drafts = [
            ArticleDraft(draft_type="review", title=f"Idempotent draft {i}", content="Body")
            for i in range(3)
        ]
        account = ChannelAccount(
            channel="example", account_name="test account", credential_type="cookie", is_active=True
        )
        session.add_all([*drafts, account])
        session.flush()
        draft_ids = [draft.id for draft in drafts]
        account_id = account.id

    app.dependency_overrides[require_reviewer] = lambda: {
        "username": "reviewer", "role": "reviewer",
    }
    try:
        with TestClient(app) as client:
            first = client.post(
                f"/api/drafts/{draft_ids[0]}/publish",
                json={"account_ids": [account_id]},
                headers={"Idempotency-Key": "single-request-key"},
            )
            repeated = client.post(
                f"/api/drafts/{draft_ids[0]}/publish",
                json={"account_ids": [account_id]},
                headers={"Idempotency-Key": "single-request-key"},
            )
            batch = client.post(
                "/api/drafts/batch-publish",
                json={"draft_ids": draft_ids[1:], "account_ids": [account_id]},
                headers={"Idempotency-Key": "batch-request-key"},
            )
            batch_repeated = client.post(
                "/api/drafts/batch-publish",
                json={"draft_ids": draft_ids[1:], "account_ids": [account_id]},
                headers={"Idempotency-Key": "batch-request-key"},
            )

        assert first.status_code == repeated.status_code == 200
        assert batch.status_code == batch_repeated.status_code == 200
        assert first.json()["tasks"][0]["record_id"] == repeated.json()["tasks"][0]["record_id"]
        assert first.json()["tasks"][0]["job_id"] == repeated.json()["tasks"][0]["job_id"]
        assert [task["record_id"] for task in batch.json()["tasks"]] == [
            task["record_id"] for task in batch_repeated.json()["tasks"]
        ]
        assert [task["job_id"] for task in batch.json()["tasks"]] == [
            task["job_id"] for task in batch_repeated.json()["tasks"]
        ]
        with get_session() as session:
            assert session.query(PublishRecord).count() == 3
            assert session.query(Job).filter(Job.kind == "publish_draft").count() == 3
    finally:
        app.dependency_overrides.pop(require_reviewer, None)


def test_status_filter_api_rejects_values_outside_shared_enum():
    app.dependency_overrides[papers_router.get_current_user] = lambda: {
        "username": "test-user", "role": "admin",
    }
    try:
        with TestClient(app) as client:
            response = client.get("/api/papers", params={"processing_status": "unknown"})
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
