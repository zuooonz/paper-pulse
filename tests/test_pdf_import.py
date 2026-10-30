import pymupdf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paper_library.models import Base, Paper
from paper_library.pdf_import import extract_pdf_metadata, process_pdf_import


TITLE = "A Synthetic Study for Testing PDF Metadata Import"
ABSTRACT = (
    "This synthetic abstract describes a complete test document. "
    "It verifies that PDF metadata remains available when Crossref cannot find a record."
)
DOI = "10.5555/pdf-metadata-fallback.2026"


def _make_pdf(path, *, doi=True, metadata_title=True):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 85), TITLE, fontsize=20)
    page.insert_text((72, 125), "Abstract", fontsize=12)
    page.insert_textbox(pymupdf.Rect(72, 140, 530, 190), ABSTRACT, fontsize=10)
    page.insert_text((72, 215), "Keywords: synthetic testing", fontsize=10)
    page.insert_text((72, 245), "Published 2026", fontsize=10)
    if doi:
        page.insert_text((72, 275), f"doi: {DOI}", fontsize=10)
    metadata = {
        "author": "Ada Lovelace; Grace Hopper",
        "creationDate": "D:20260201000000",
    }
    if metadata_title:
        metadata["title"] = TITLE
    doc.set_metadata(metadata)
    doc.save(path)
    doc.close()


def _session_factory(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_pdf_crossref_failure_falls_back_to_pdf_metadata(tmp_path, monkeypatch):
    source = tmp_path / "fallback.pdf"
    _make_pdf(source)
    archive = tmp_path / "archive"
    engine, factory = _session_factory(tmp_path / "fallback.db")

    def failed_crossref(*args, **kwargs):
        return {"_error": "unavailable"}

    monkeypatch.setattr("paper_library.pdf_import.fetch_metadata_by_doi", failed_crossref)
    monkeypatch.setattr("paper_library.pdf_import.time.sleep", lambda *_: None)

    try:
        with factory() as session:
            result = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=archive,
                workspace=str(tmp_path),
            )
            session.commit()

            paper = session.get(Paper, result.paper_id)
            assert paper is not None
            assert result.fallback is True
            assert result.status == "created"
            assert paper.doi == DOI
            assert paper.title == TITLE
            assert paper.year == 2026
            assert paper.abstract.startswith("This synthetic abstract")
            assert paper.original_data["authors"] == ["Ada Lovelace", "Grace Hopper"]
            assert paper.source == "pdf_metadata"
            assert paper.processing_status == "done"
            assert paper.review_outcome == "unreviewed"
            assert paper.processing_error is None
            assert paper.has_pdf is True
            assert paper.pdf_text_extracted is True
            assert TITLE in paper.extracted_text
            assert paper.pdf_storage_path
    finally:
        engine.dispose()


def test_pdf_without_doi_uses_metadata_fallback(tmp_path, monkeypatch):
    source = tmp_path / "no-doi.pdf"
    _make_pdf(source, doi=False, metadata_title=False)
    engine, factory = _session_factory(tmp_path / "no-doi.db")

    def unexpected_crossref(*args, **kwargs):
        raise AssertionError("Crossref must not be called when no DOI is present")

    monkeypatch.setattr("paper_library.pdf_import.fetch_metadata_by_doi", unexpected_crossref)
    monkeypatch.setattr("paper_library.pdf_import.time.sleep", lambda *_: None)

    try:
        with factory() as session:
            result = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
            )
            session.commit()

            paper = session.get(Paper, result.paper_id)
            assert result.fallback is True
            assert paper is not None
            assert paper.doi is None
            assert paper.title == TITLE
            assert paper.year == 2026
            assert paper.processing_status == "done"
            assert paper.review_outcome == "unreviewed"
    finally:
        engine.dispose()


def test_existing_paper_pdf_metadata_fallback_enters_pending(tmp_path, monkeypatch):
    source = tmp_path / "existing-fallback.pdf"
    _make_pdf(source)
    engine, factory = _session_factory(tmp_path / "existing-fallback.db")

    monkeypatch.setattr(
        "paper_library.pdf_import.fetch_metadata_by_doi",
        lambda *args, **kwargs: {"_error": "unavailable"},
    )
    monkeypatch.setattr("paper_library.pdf_import.time.sleep", lambda *_: None)

    try:
        with factory() as session:
            paper = Paper(
                doi=DOI,
                title=TITLE,
                processing_status="pending",
                doi_meta=None,
            )
            session.add(paper)
            session.flush()

            result = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
            )
            session.commit()

            saved = session.get(Paper, paper.id)
            assert result.existing is True
            assert result.fallback is True
            assert saved.processing_status == "done"
            assert saved.review_outcome == "unreviewed"
            assert saved.doi_meta is None
    finally:
        engine.dispose()


def test_crossref_success_keeps_normal_import_flow(tmp_path, monkeypatch):
    source = tmp_path / "crossref.pdf"
    _make_pdf(source)
    engine, factory = _session_factory(tmp_path / "crossref.db")
    raw = {
        "container-title": ["Synthetic Journal"],
        "published-print": {"date-parts": [[2026]]},
        "volume": "2",
        "issue": "1",
        "page": "1-4",
        "author": [{"given": "Ada", "family": "Lovelace"}],
        "title": [TITLE],
        "abstract": ABSTRACT,
    }
    crossref = {
        "doi": DOI,
        "title": TITLE,
        "year": 2026,
        "abstract": ABSTRACT,
        "journal": {"title": "Synthetic Journal"},
        "raw": raw,
    }

    monkeypatch.setattr("paper_library.pdf_import.fetch_metadata_by_doi", lambda *a, **k: crossref)
    monkeypatch.setattr("core.crossref_api.fetch_metadata_by_doi", lambda *a, **k: crossref)
    monkeypatch.setattr("paper_library.pdf_import.time.sleep", lambda *_: None)

    try:
        with factory() as session:
            result = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
            )
            session.commit()

            paper = session.get(Paper, result.paper_id)
            assert result.fallback is False
            assert paper is not None
            assert paper.source == "pdf_import"
            assert paper.doi_meta == raw
            assert paper.processing_status == "done"
            assert paper.review_outcome == "unreviewed"
    finally:
        engine.dispose()


def test_pdf_title_uses_largest_first_page_line_when_metadata_title_missing(tmp_path):
    source = tmp_path / "title-from-page.pdf"
    _make_pdf(source, metadata_title=False)
    extracted = extract_pdf_metadata(source, "")
    assert extracted["title"] == TITLE
