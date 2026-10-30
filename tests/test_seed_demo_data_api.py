"""TestClient contract checks for the seeded paper detail payload."""

from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.dependencies import get_current_user
from api.main import app
from api.routers.papers import detail as papers_detail
from paper_library.models import Base, Paper


def test_seeded_paper_detail_returns_frontend_array_fields(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed-detail.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(
            title="Quantitative MRI repeatability assessment (Cohort 01)",
            title_zh="定量磁共振重复性评估（队列 01）",
            doi="10.5555/demo.qmri.detail-test",
            processing_status="done", review_outcome="approved",
            entity_label="Example Imaging",
            equipment_brand=["Example Imaging"],
            research_fields=["方法学"],
            research_topics=["方法学"],
            subject_diseases=["神经退行性疾病"],
            paradigm=["静息态扫描"],
            paradigms=["静息态扫描"],
            multimodal_methods=[],
            keywords=["qMRI", "repeatability"],
            keywords_zh=["定量磁共振", "重复性"],
            tags=[],
            extracted_images=[],
        )
        session.add(paper)
        session.commit()
        paper_id = paper.id

    @contextmanager
    def test_session(*_args, **_kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(papers_detail, "get_session", test_session)
    monkeypatch.setattr(papers_detail, "get_session", test_session)
    monkeypatch.setattr(papers_detail, "get_draft_ids_for_paper", lambda *_args: [])
    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: {
        "username": "test-user", "role": "admin",
    })
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/papers/{paper_id}")
        assert response.status_code == 200
        detail = response.json()
        for field in (
            "equipment_brand", "research_fields", "research_topics", "subject_diseases",
            "paradigm", "paradigms", "multimodal_methods", "keywords", "keywords_zh", "tags",
        ):
            assert isinstance(detail[field], list), field
            assert all(isinstance(value, str) for value in detail[field]), field
        assert detail["equipment_brand"] == ["Example Imaging"]
    finally:
        engine.dispose()
