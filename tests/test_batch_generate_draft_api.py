from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.main import app
from api.routers import papers as papers_router
from api.routers.papers import generation as papers_generation
from core import entity_detector
from paper_library.models import ArticleDraft, Base, Paper, draft_papers


def test_batch_generate_draft_force_regen_uses_single_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'batch-generate.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    try:
        with factory() as session:
            paper = Paper(
                title="Test paper",
                year=2025,
                journal_name="Test Journal",
                abstract="Summary for test paper.",
                processing_status="done", review_outcome="approved",
                article_status="generated",
                entity_label=entity_detector.get_target_label(),
            )
            old_draft = ArticleDraft(
                draft_type="single_daily",
                title="Old draft",
                content="Old content.",
                status="draft",
            )
            session.add_all([paper, old_draft])
            session.flush()
            paper_id = paper.id
            old_draft_id = old_draft.id
            session.execute(
                draft_papers.insert().values(draft_id=old_draft_id, paper_id=paper_id)
            )
            session.commit()

        @contextmanager
        def _session_context(*, workspace=None, config_dir=None):
            # Mirror paper_library.db.get_session: commit on success, roll back on error.
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        monkeypatch.setattr(papers_generation, "get_session", _session_context)
        monkeypatch.setattr(papers_generation.settings, "workspace", str(tmp_path))
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
        monkeypatch.setattr(
            papers_generation.OpenAICompatibleClient,
            "chat",
            lambda _self, _model, _messages, **_kwargs: "# New draft\n\nGenerated content.",
        )
        monkeypatch.setitem(
            app.dependency_overrides,
            papers_router.get_current_user,
            lambda: {"username": "test-user", "role": "admin"},
        )

        client = TestClient(app)
        try:
            response = client.post(
                "/api/papers/batch-generate-draft",
                json={
                    "paper_ids": [paper_id],
                    "profile": "default",
                    "model": "test-model",
                    "force_regen": True,
                },
            )
        finally:
            client.close()

        assert response.status_code == 200, response.text
        assert response.json() == {"success": 1, "failed": 0, "skipped": 0}

        with Session(engine) as session:
            # SQLite may reuse the deleted row id, so check by content instead.
            assert session.scalars(select(ArticleDraft).where(ArticleDraft.title == "Old draft")).first() is None
            drafts = session.scalars(select(ArticleDraft)).all()
            assert len(drafts) == 1
            new_draft = drafts[0]
            assert new_draft.title == "New draft"
            linked_paper_ids = session.scalars(
                select(draft_papers.c.paper_id).where(
                    draft_papers.c.draft_id == new_draft.id
                )
            ).all()
            assert linked_paper_ids == [paper_id]
            assert session.get(Paper, paper_id).article_status == "generated"
    finally:
        app.dependency_overrides.pop(papers_router.get_current_user, None)
        engine.dispose()
