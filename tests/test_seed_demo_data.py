from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from core.entity_detector import load_entities_config
from core.taxonomy_config import dimension_items, load_taxonomy_config
from paper_library.db import get_session
from paper_library.journal_enrichment import JcrMatcher
from paper_library.models import (
    AffiliationGeoEvidence,
    AffiliationLocation,
    ArticleDraft,
    Author,
    Base,
    ChannelAccount,
    JournalMetric,
    Paper,
    PaperAuthorMention,
    PublishRecord,
    paper_authors,
)
from scripts.seed_demo_data import reset_demo_data, seed_demo_data


ROOT = Path(__file__).resolve().parents[1]


def _matcher(monkeypatch) -> JcrMatcher:
    monkeypatch.delenv("JOURNAL_METRICS_DB", raising=False)
    monkeypatch.delenv("REFERENCE_CONFIG", raising=False)
    return JcrMatcher(
        jcr_db_path=ROOT / "data" / "reference" / "jcr.db",
        config_path=ROOT / "config" / "reference.yaml",
        workspace=ROOT,
    ).load()


def test_seed_demo_data_is_idempotent_and_reset_preserves_other_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(engine)
    matcher = _matcher(monkeypatch)

    with Session(engine, expire_on_commit=False) as session:
        first = seed_demo_data(session, count=24, matcher=matcher, workspace=tmp_path)
        session.commit()
        assert first == {"papers_created": 24, "papers_total": 24, "drafts": 4}

        demo_papers = list(session.scalars(select(Paper).where(Paper.source == "demo_seed")).all())
        assert len(demo_papers) == 24
        assert len({paper.title for paper in demo_papers}) == 24
        assert len({paper.title_zh for paper in demo_papers}) == 24
        assert len({paper.doi for paper in demo_papers}) == 24
        assert len({paper.abstract for paper in demo_papers}) == 24
        assert len({paper.abstract_zh for paper in demo_papers}) == 24
        assert {paper.processing_status for paper in demo_papers} == {
            "done", "pending", "processing", "failed",
        }
        assert {paper.review_outcome for paper in demo_papers} == {
            "approved", "unreviewed", "excluded",
        }
        assert sum(paper.review_outcome == "approved" for paper in demo_papers) == 17
        assert sum(paper.processing_status == "done" for paper in demo_papers) == 21
        assert sum(not paper.metadata_complete for paper in demo_papers) == 1
        assert sum(paper.has_pdf for paper in demo_papers) == 20
        assert sum(not paper.has_pdf for paper in demo_papers) == 4
        assert all(
            (tmp_path / paper.pdf_storage_path).is_file()
            for paper in demo_papers if paper.has_pdf
        )
        assert all(paper.journal_id is not None for paper in demo_papers)
        assert session.scalar(select(func.count(JournalMetric.id))) >= 6
        assert all(2021 <= paper.year <= 2026 for paper in demo_papers)
        assert sum(bool(paper.abstract and paper.abstract_zh) for paper in demo_papers) == 23
        assert all(paper.doi.startswith("10.5555/demo.") for paper in demo_papers)
        string_list_fields = (
            "equipment_brand", "research_fields", "research_topics", "subject_diseases",
            "paradigm", "paradigms", "multimodal_methods", "keywords", "keywords_zh", "tags",
        )
        for paper in demo_papers:
            for field in string_list_fields:
                value = getattr(paper, field)
                assert isinstance(value, list) and all(isinstance(item, str) for item in value), field
            assert paper.equipment_brand == [paper.entity_label]
            assert paper.extracted_images == []
            assert paper.multimodal_relations == []
            assert paper.doi_meta is None
            assert isinstance(paper.multimodal_techniques, dict)
            assert paper.original_data == {"source": "demo_seed", "fictional": True}
            assert all("demo_seed" not in value for value in paper.keywords + paper.keywords_zh)
        taxonomy = load_taxonomy_config(ROOT / "config")
        for dimension, attribute in (
            ("research_topics", "research_topics"),
            ("diseases", "subject_disease"),
            ("paradigms", "paradigms"),
            ("study_design", "study_design"),
        ):
            allowed = {
                str(item.get("value") or item["label"])
                for item in dimension_items(taxonomy, dimension)
            }
            for paper in demo_papers:
                values = getattr(paper, attribute)
                if isinstance(values, list):
                    assert set(values) <= allowed
                else:
                    assert values in allowed
        configured_entities = load_entities_config(config_path=ROOT / "config" / "entities.yaml")
        allowed_entities = {
            str(item.get("label") or key)
            for key, item in configured_entities["entities"].items()
        }
        assert {paper.entity_label for paper in demo_papers} <= allowed_entities
        assert {paper.entity_label for paper in demo_papers} == {"qMRLab", "hMRI toolbox"}
        assert any("qMRLab software" in paper.abstract for paper in demo_papers)
        assert any("hMRI toolbox for SPM" in paper.abstract for paper in demo_papers)

        for paper in demo_papers:
            author_count = session.scalar(select(func.count()).select_from(paper_authors).where(
                paper_authors.c.paper_id == paper.id,
            ))
            assert 3 <= author_count <= 6
            assert session.scalar(select(func.count()).select_from(paper_authors).where(
                paper_authors.c.paper_id == paper.id,
                paper_authors.c.is_first.is_(True),
            )) == 1
            assert session.scalar(select(func.count()).select_from(paper_authors).where(
                paper_authors.c.paper_id == paper.id,
                paper_authors.c.is_corresponding.is_(True),
            )) == 1

        drafts = list(session.scalars(select(ArticleDraft)).all())
        assert len(drafts) == 4
        assert all(isinstance(draft.meta, dict) and draft.meta.get("source") == "demo_seed" for draft in drafts)
        assert {draft.status for draft in drafts} == {"draft", "approved", "published"}
        assert session.scalar(select(func.count(PublishRecord.id))) == 0
        assert session.scalar(select(func.count(AffiliationLocation.id))) >= 6
        assert session.scalar(select(func.count(AffiliationGeoEvidence.id)).where(
            AffiliationGeoEvidence.provider == "demo_seed",
            AffiliationGeoEvidence.lat.is_not(None),
            AffiliationGeoEvidence.lng.is_not(None),
        )) >= 6
        assert session.scalar(select(func.count(PaperAuthorMention.id)).where(
            PaperAuthorMention.source_type == "demo_seed",
        )) >= 24 * 3
        assert session.scalar(select(func.count(Author.id)).where(
            Author.orcid == "0000-0002-1825-0097",
        )) == 1

        seeded_snapshot = [
            (
                paper.doi, paper.title, paper.title_zh, paper.abstract, paper.abstract_zh,
                paper.equipment_brand, paper.research_fields, paper.research_topics,
                paper.subject_diseases, paper.paradigm, paper.paradigms,
                paper.multimodal_methods, paper.multimodal_relations, paper.keywords,
                paper.keywords_zh, paper.tags, paper.extracted_images, paper.doi_meta,
                paper.multimodal_techniques, paper.original_data,
            )
            for paper in sorted(demo_papers, key=lambda item: item.doi)
        ]

        second = seed_demo_data(session, count=24, matcher=matcher, workspace=tmp_path)
        session.commit()
        assert second == {"papers_created": 0, "papers_total": 24, "drafts": 4}
        assert session.scalar(select(func.count(Paper.id)).where(Paper.source == "demo_seed")) == 24
        assert session.scalar(select(func.count(ArticleDraft.id))) == 4
        assert session.scalar(select(func.count(PublishRecord.id))) == 0

        other_paper = Paper(title="非演示论文", source="manual", processing_status="done", review_outcome="approved")
        other_draft = ArticleDraft(
            draft_type="manual_upload", title="非演示草稿", content="保留", meta={"source": "manual"},
        )
        other_account = ChannelAccount(
            channel="csdn", account_name="用户账号", credential_type="cookie", note="user-owned",
        )
        other_author = Author(name="Other Fictional Author", name_zh="其他虚构作者")
        session.add_all([other_paper, other_draft, other_account, other_author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=other_paper.id, author_id=other_author.id,
            author_order=0, is_first=True, is_corresponding=True,
        ))

        reset = reset_demo_data(session, workspace=tmp_path)
        session.commit()
        assert reset["papers_removed"] == 24
        assert reset["publish_records_removed"] == 0
        assert session.scalar(select(func.count(Paper.id)).where(Paper.source == "demo_seed")) == 0
        assert session.get(Paper, other_paper.id) is not None
        assert session.get(ArticleDraft, other_draft.id) is not None
        assert session.get(ChannelAccount, other_account.id) is not None
        assert session.get(Author, other_author.id) is not None
        assert session.scalar(select(func.count(PublishRecord.id))) == 0

        rerun = seed_demo_data(session, count=24, matcher=matcher, workspace=tmp_path)
        session.commit()
        assert rerun == {"papers_created": 24, "papers_total": 24, "drafts": 4}
        rerun_papers = list(session.scalars(
            select(Paper).where(Paper.source == "demo_seed").order_by(Paper.doi)
        ).all())
        rerun_snapshot = [
            (
                paper.doi, paper.title, paper.title_zh, paper.abstract, paper.abstract_zh,
                paper.equipment_brand, paper.research_fields, paper.research_topics,
                paper.subject_diseases, paper.paradigm, paper.paradigms,
                paper.multimodal_methods, paper.multimodal_relations, paper.keywords,
                paper.keywords_zh, paper.tags, paper.extracted_images, paper.doi_meta,
                paper.multimodal_techniques, paper.original_data,
            )
            for paper in rerun_papers
        ]
        assert rerun_snapshot == seeded_snapshot
        assert session.get(Paper, other_paper.id) is not None
        assert session.get(ArticleDraft, other_draft.id) is not None
        assert session.get(ChannelAccount, other_account.id) is not None
        assert session.get(Author, other_author.id) is not None

    engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith(("postgresql://", "postgresql+")),
    reason="仅在配置 PostgreSQL TEST_DATABASE_URL 时运行",
)
def test_seed_demo_data_runs_on_test_postgres(monkeypatch):
    matcher = _matcher(monkeypatch)
    with get_session(str(ROOT), "config") as session:
        result = seed_demo_data(session, count=6, matcher=matcher)
        assert result["papers_total"] == 6
        assert session.scalar(select(func.count(Paper.id)).where(Paper.source == "demo_seed")) == 6
        reset_demo_data(session)
        assert session.scalar(select(func.count(Paper.id)).where(Paper.source == "demo_seed")) == 0
