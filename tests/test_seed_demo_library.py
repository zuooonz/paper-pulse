from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from paper_library.journal_enrichment import JcrMatcher
from paper_library.models import Base, Paper
from scripts.seed_demo_data import reset_demo_data
from scripts.seed_demo_library import seed_demo_library


ROOT = Path(__file__).resolve().parents[1]


def test_demo_library_seed_is_idempotent_and_prefixed(tmp_path, monkeypatch):
    monkeypatch.delenv("JOURNAL_METRICS_DB", raising=False)
    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    matcher = JcrMatcher(
        jcr_db_path=ROOT / "data" / "reference" / "jcr.db",
        config_path=ROOT / "config" / "reference.yaml",
        workspace=ROOT,
    ).load()
    engine = create_engine(f"sqlite:///{tmp_path / 'demo-library.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = seed_demo_library(session, matcher=matcher, workspace=tmp_path)
        session.commit()
        assert result == {"papers_created": 4, "papers_total": 4, "drafts": 4}
        papers = list(session.scalars(select(Paper).where(Paper.source == "demo_seed")).all())
        assert len(papers) == 4
        assert all(paper.title.startswith("[demo]") for paper in papers)
        assert len({paper.title for paper in papers}) == 4
        assert sum(1 for paper in papers if paper.has_pdf) == 1
        assert len(list((tmp_path / "data" / "pdf").glob("*.pdf"))) == 1

        again = seed_demo_library(session, matcher=matcher, workspace=tmp_path)
        session.commit()
        assert again["papers_created"] == 0
        assert reset_demo_data(session, tmp_path)["papers_removed"] == 4
        session.commit()
