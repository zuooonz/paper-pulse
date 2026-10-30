from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from api.routers import papers as papers_router
from api.routers.papers import detail as papers_detail
from paper_library.models import (
    ArticleDraft,
    Author,
    AuthorIdentifier,
    Base,
    Paper,
    PaperAuthorMention,
    draft_papers,
    paper_authors,
)


def _sqlite_engine(path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _session_context(factory):
    @contextmanager
    def _context(*_args, **_kwargs):
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _context


def _request():
    return Request({"type": "http", "method": "DELETE", "path": "/", "headers": []})


@pytest.fixture
def deletion_store(tmp_path, monkeypatch):
    paper_engine = _sqlite_engine(tmp_path / "app.db")
    Base.metadata.create_all(paper_engine)
    paper_factory = sessionmaker(bind=paper_engine)
    draft_factory = paper_factory
    monkeypatch.setattr(papers_detail, "get_session", _session_context(paper_factory))
    monkeypatch.setattr(papers_detail, "get_session", _session_context(draft_factory))
    monkeypatch.setattr(papers_detail.settings, "workspace", str(tmp_path))
    yield tmp_path, paper_factory, draft_factory
    paper_engine.dispose()


def _seed_linked_paper(store):
    workspace, paper_factory, draft_factory = store
    with paper_factory() as session:
        target = Paper(
            title="Preprint",
            internal_id=656,
            pdf_storage_path="data/pdf/preprint.pdf",
            processing_status="done", review_outcome="approved",
        )
        other = Paper(title="Published version", internal_id=753, processing_status="done", review_outcome="approved")
        unique_author = Author(name="Unique Author", orcid="0000-0000-0000-0001")
        shared_author = Author(name="Shared Author", orcid="0000-0002-1825-0097")
        session.add_all([target, other, unique_author, shared_author])
        session.flush()
        session.execute(paper_authors.insert(), [
            {"paper_id": target.id, "author_id": unique_author.id, "author_order": 0},
            {"paper_id": target.id, "author_id": shared_author.id, "author_order": 1},
            {"paper_id": other.id, "author_id": shared_author.id, "author_order": 0},
        ])
        session.add_all([
            PaperAuthorMention(
                paper_id=target.id, author_id=unique_author.id,
                name_raw="Unique Author", source_type="crossref",
            ),
            PaperAuthorMention(
                paper_id=target.id, author_id=shared_author.id,
                name_raw="Shared Author", source_type="crossref",
            ),
            PaperAuthorMention(
                paper_id=other.id, author_id=shared_author.id,
                name_raw="Shared Author", source_type="crossref",
            ),
            AuthorIdentifier(
                author_id=unique_author.id, identifier_type="orcid",
                identifier_value=unique_author.orcid, normalized_value=unique_author.orcid,
                source_type="crossref", source_paper_id=target.id,
            ),
            AuthorIdentifier(
                author_id=shared_author.id, identifier_type="orcid",
                identifier_value=shared_author.orcid, normalized_value=shared_author.orcid,
                source_type="crossref", source_paper_id=target.id,
            ),
        ])
        session.commit()
        ids = {
            "target": target.id,
            "other": other.id,
            "unique_author": unique_author.id,
            "shared_author": shared_author.id,
        }

    with draft_factory() as session:
        draft = ArticleDraft(draft_type="single_daily", title="Draft", content="Content")
        session.add(draft)
        session.flush()
        session.execute(draft_papers.insert().values(draft_id=draft.id, paper_id=ids["target"]))
        session.commit()
        ids["draft"] = draft.id

    pdf = workspace / "data" / "pdf" / "preprint.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"pdf")
    image_dir = workspace / "data" / "images" / str(ids["target"])
    image_dir.mkdir(parents=True)
    (image_dir / "figure.png").write_bytes(b"image")
    return ids, pdf, image_dir


def test_delete_paper_cleans_relations_and_only_removes_orphan_authors(deletion_store):
    _workspace, paper_factory, draft_factory = deletion_store
    ids, pdf, image_dir = _seed_linked_paper(deletion_store)

    request = _request()
    body = papers_router.DeletePaperBody(reason="与正式发表版本重复")
    result = papers_router.delete_paper(ids["target"], body=body, request=request, user={"role": "admin"})

    assert result == {
        "deleted": True,
        "paper_id": ids["target"],
        "orphan_authors_deleted": 1,
    }
    assert request.state.audit_details["paper_internal_ids"] == [656]
    assert request.state.audit_details["delete_reason"] == "与正式发表版本重复"
    assert not pdf.exists()
    assert not image_dir.exists()
    with paper_factory() as session:
        assert session.get(Paper, ids["target"]) is None
        assert session.get(Paper, ids["other"]) is not None
        assert session.get(Author, ids["unique_author"]) is None
        assert session.get(Author, ids["shared_author"]) is not None
        shared_identifier = session.scalar(select(AuthorIdentifier).where(
            AuthorIdentifier.author_id == ids["shared_author"]
        ))
        assert shared_identifier is not None
        assert shared_identifier.source_paper_id is None
    with draft_factory() as session:
        assert session.get(ArticleDraft, ids["draft"]) is not None
        assert session.execute(select(draft_papers).where(
            draft_papers.c.paper_id == ids["target"]
        )).first() is None


def test_delete_constraint_failure_preserves_database_links_and_files(deletion_store):
    _workspace, paper_factory, draft_factory = deletion_store
    ids, pdf, image_dir = _seed_linked_paper(deletion_store)
    with paper_factory() as session:
        session.execute(text(
            "CREATE TABLE unexpected_paper_links ("
            "id INTEGER PRIMARY KEY, paper_id INTEGER NOT NULL REFERENCES papers(id))"
        ))
        session.execute(
            text("INSERT INTO unexpected_paper_links (paper_id) VALUES (:paper_id)"),
            {"paper_id": ids["target"]},
        )
        session.commit()

    with pytest.raises(HTTPException) as exc_info:
        papers_router.delete_paper(
            ids["target"],
            body=papers_router.DeletePaperBody(reason="测试约束失败"),
            request=_request(),
            user={"role": "admin"},
        )

    assert exc_info.value.status_code == 409
    assert pdf.exists()
    assert image_dir.exists()
    with paper_factory() as session:
        assert session.get(Paper, ids["target"]) is not None
        assert session.get(Author, ids["unique_author"]) is not None
        assert session.execute(select(paper_authors).where(
            paper_authors.c.paper_id == ids["target"]
        )).first() is not None
        assert session.scalar(select(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == ids["target"]
        )) is not None
    with draft_factory() as session:
        assert session.execute(select(draft_papers).where(
            draft_papers.c.paper_id == ids["target"]
        )).first() is not None


def test_batch_delete_reuses_safe_cleanup_and_deduplicates_ids(deletion_store):
    _workspace, paper_factory, draft_factory = deletion_store
    ids, pdf, image_dir = _seed_linked_paper(deletion_store)

    request = _request()
    result = papers_router.batch_delete_papers(
        papers_router.BatchDeleteBody(
            paper_ids=[ids["target"], ids["target"], 999999],
            reason="批量清理重复论文",
        ),
        request=request,
        user={"role": "admin"},
    )

    assert result == {
        "deleted": 1,
        "not_found": [999999],
        "orphan_authors_deleted": 1,
    }
    assert request.state.audit_details["paper_internal_ids"] == [656]
    assert request.state.audit_details["delete_reason"] == "批量清理重复论文"
    assert not pdf.exists()
    assert not image_dir.exists()
    with paper_factory() as session:
        assert session.get(Paper, ids["target"]) is None
        assert session.get(Author, ids["unique_author"]) is None
        assert session.get(Author, ids["shared_author"]) is not None
    with draft_factory() as session:
        assert session.get(ArticleDraft, ids["draft"]) is not None
        assert session.execute(select(draft_papers).where(
            draft_papers.c.paper_id == ids["target"]
        )).first() is None


def test_batch_delete_route_is_declared_before_dynamic_paper_route():
    delete_paths = [
        route.path for route in papers_detail.router.routes
        if "DELETE" in (route.methods or set())
    ]
    assert delete_paths.index("/batch") < delete_paths.index("/{paper_id}")


@pytest.mark.parametrize("reason", ["", " ", "x", "x" * 501])
def test_delete_reason_is_required_and_bounded(reason):
    with pytest.raises(ValidationError):
        papers_router.DeletePaperBody(reason=reason)


def test_delete_reason_is_trimmed_before_audit_storage():
    body = papers_router.DeletePaperBody(reason="  重复论文  ")
    assert body.reason == "重复论文"
