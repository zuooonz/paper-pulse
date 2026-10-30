import json
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.models import Author, Base, Paper, PaperAuthorMention, paper_authors


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "crossref_sample.json"


def _crossref_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_normalize_authors_creates_identities_relations_and_snapshots():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    metadata = _crossref_fixture()

    with Session(engine) as session:
        paper = Paper(doi=metadata["DOI"], title=metadata["title"][0])
        session.add(paper)
        session.flush()

        assert normalize_authors_for_paper(metadata, paper.id, session) == 3
        session.flush()

        authors = session.scalars(
            select(Author)
            .join(paper_authors, paper_authors.c.author_id == Author.id)
            .where(paper_authors.c.paper_id == paper.id)
            .order_by(paper_authors.c.author_order)
        ).all()
        links = session.execute(
            select(paper_authors)
            .where(paper_authors.c.paper_id == paper.id)
            .order_by(paper_authors.c.author_order)
        ).all()
        mentions = session.scalars(
            select(PaperAuthorMention)
            .where(PaperAuthorMention.paper_id == paper.id)
            .order_by(PaperAuthorMention.name_raw)
        ).all()

        assert [author.name for author in authors] == [
            "Alex Example", "Riley Sample", "Jordan Researcher",
        ]
        assert [link.author_order for link in links] == [0, 1, 2]
        assert links[0].is_first is True
        assert len(mentions) == 3
        assert {mention.source_type for mention in mentions} == {"crossref_snapshot"}
        assert {mention.verification_status for mention in mentions} == {"source_recorded"}
        jordan = next(author for author in authors if author.name == "Jordan Researcher")
        assert jordan.orcid == "0000-0002-1825-0097"


def test_normalize_authors_is_idempotent_for_fixture_metadata():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    metadata = _crossref_fixture()

    with Session(engine) as session:
        paper = Paper(doi=metadata["DOI"], title=metadata["title"][0])
        session.add(paper)
        session.flush()

        normalize_authors_for_paper(metadata, paper.id, session)
        session.flush()
        first_author_ids = session.scalars(select(Author.id).order_by(Author.id)).all()

        normalize_authors_for_paper(metadata, paper.id, session)
        session.flush()

        assert session.scalar(select(func.count()).select_from(Author)) == 3
        assert session.scalar(select(func.count()).select_from(paper_authors)) == 3
        assert session.scalar(select(func.count()).select_from(PaperAuthorMention)) == 3
        assert session.scalars(select(Author.id).order_by(Author.id)).all() == first_author_ids


def test_normalize_authors_reuses_existing_paper_link_for_ambiguous_name():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    metadata = {
        "author": [{"given": "Alex", "family": "Example", "sequence": "first"}],
    }

    with Session(engine) as session:
        paper = Paper(doi="10.5555/ambiguous-author", title="Synthetic paper")
        session.add_all([
            paper,
            Author(name="Alex Example", affiliation="Example Unit A"),
            Author(name="Alex Example", affiliation="Example Unit B"),
        ])
        session.flush()

        normalize_authors_for_paper(metadata, paper.id, session)
        session.flush()
        linked_author_id = session.scalar(
            select(paper_authors.c.author_id).where(paper_authors.c.paper_id == paper.id)
        )
        normalize_authors_for_paper(metadata, paper.id, session)
        session.flush()

        assert session.scalar(select(func.count()).select_from(Author)) == 3
        assert session.scalar(select(func.count()).select_from(paper_authors)) == 1
        assert session.scalar(select(paper_authors.c.author_id).where(
            paper_authors.c.paper_id == paper.id
        )) == linked_author_id


def test_search_domain_ingest_generates_authors_from_crossref(monkeypatch, tmp_path):
    from scripts import search_domain_papers
    from paper_library.db import get_session

    metadata = _crossref_fixture()
    monkeypatch.setattr(search_domain_papers, "fetch_metadata_by_doi", lambda _doi: metadata)

    stats = search_domain_papers.ingest_papers(
        [{
            "doi": metadata["DOI"],
            "title": metadata["title"][0],
            "year": 2024,
            "journal": metadata["container-title"][0],
            "abstract": "Synthetic abstract to avoid a metadata retry.",
            "source": "fixture",
        }],
        workspace=str(tmp_path),
        enrich=True,
    )

    assert stats == {"ingested": 1, "enriched": 1, "failed": 0}
    with get_session(str(tmp_path)) as session:
        paper = session.scalar(select(Paper).where(Paper.doi == metadata["DOI"]))
        assert paper is not None
        assert session.scalar(select(func.count()).select_from(paper_authors).where(
            paper_authors.c.paper_id == paper.id
        )) == 3
        assert session.scalar(select(func.count()).select_from(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == paper.id
        )) == 3
