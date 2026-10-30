"""Synthetic PDF and database edge cases for PDF import helpers."""

from __future__ import annotations

import pymupdf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paper_library.models import Base, Paper


TITLE = "Synthetic Methods for Reproducible Metadata Tests"
DOI = "10.5555/reproducible-metadata.2026"


def _make_pdf(path, *, title=TITLE, doi=DOI, pages=1):
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        if index == 0:
            page.insert_text((72, 80), title, fontsize=18)
            page.insert_text((72, 120), "Abstract", fontsize=11)
            page.insert_textbox(
                pymupdf.Rect(72, 135, 520, 185),
                "This synthetic abstract covers import edge cases and stays offline.",
                fontsize=10,
            )
            page.insert_text((72, 215), "Keywords: reproducible tests", fontsize=10)
            page.insert_text((72, 245), "Published 2026", fontsize=10)
            if doi:
                page.insert_text((72, 275), f"doi: {doi}", fontsize=10)
    document.save(path)
    document.close()


def _engine_and_factory(path):
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def _crossref(title=TITLE):
    return {
        "doi": DOI,
        "title": title,
        "year": 2026,
        "abstract": "Synthetic abstract.",
        "journal": {"title": "Synthetic Journal"},
        "raw": {
            "container-title": ["Synthetic Journal"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
        },
    }


def _offline(monkeypatch, metadata=None):
    from paper_library import pdf_import

    monkeypatch.setattr(pdf_import, "fetch_metadata_by_doi", lambda *_args, **_kwargs: metadata or _crossref())
    monkeypatch.setattr(pdf_import.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(pdf_import, "_get_jcr_matcher", lambda *_args: None)
    monkeypatch.setattr(pdf_import, "enrich_paper_dates", lambda *_args: None)
    monkeypatch.setattr(pdf_import, "enrich_missing_abstract", lambda *_args, **_kwargs: None)


def test_extract_pdf_text_limits_pages_and_handles_invalid_file(tmp_path):
    from paper_library.pdf_import import extract_pdf_text

    source = tmp_path / "many-pages.pdf"
    _make_pdf(source, pages=7)
    text = extract_pdf_text(source, max_chars=40)
    assert len(text) <= 40
    assert "Synthetic Methods" in text

    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"invalid")
    assert extract_pdf_text(invalid) == ""


def test_metadata_and_import_reject_bad_pdf_and_invalid_expected_doi(tmp_path, monkeypatch):
    from paper_library.pdf_import import extract_pdf_metadata, process_pdf_import

    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a PDF")
    metadata = extract_pdf_metadata(invalid)
    assert metadata["_valid_pdf"] is False

    engine, factory = _engine_and_factory(tmp_path / "edges.db")
    try:
        with factory() as session:
            result = process_pdf_import(
                invalid, session, next_id=None, pdf_dir=tmp_path / "archive", workspace=str(tmp_path)
            )
            assert result.status == "error"
            assert result.error_msg == "无法读取 PDF 文件"

        source = tmp_path / "valid.pdf"
        _make_pdf(source)
        with factory() as session:
            result = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
                expected_doi="not-a-doi",
            )
            assert result.status == "error"
            assert result.error_msg.startswith("DOI 格式无效")
    finally:
        engine.dispose()


def test_crossref_title_mismatch_can_be_skipped_explicitly(tmp_path, monkeypatch):
    from paper_library.pdf_import import process_pdf_import

    _offline(monkeypatch, _crossref("A Completely Unrelated Quantum Measurement Study Title"))
    source = tmp_path / "mismatch.pdf"
    _make_pdf(source)
    engine, factory = _engine_and_factory(tmp_path / "mismatch.db")
    try:
        with factory() as session:
            rejected = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
            )
            assert rejected.status == "mismatch"
            assert rejected.paper_id is None

        with factory() as session:
            accepted = process_pdf_import(
                source,
                session,
                next_id=11,
                pdf_dir=tmp_path / "archive",
                skip_title_check=True,
                workspace=str(tmp_path),
            )
            session.commit()
            assert accepted.status == "created"
            assert accepted.paper_id is not None
            assert session.get(Paper, accepted.paper_id).internal_id == 11
    finally:
        engine.dispose()


def test_existing_pdf_is_preserved_or_replaced_as_requested(tmp_path, monkeypatch):
    from paper_library.pdf_import import process_pdf_import

    _offline(monkeypatch)
    source = tmp_path / "incoming.pdf"
    _make_pdf(source, pages=2)
    workspace = tmp_path / "workspace"
    existing_pdf = workspace / "data" / "papers" / "existing.pdf"
    existing_pdf.parent.mkdir(parents=True)
    existing_pdf.write_bytes(b"previous PDF")
    engine, factory = _engine_and_factory(tmp_path / "existing.db")
    try:
        with factory() as session:
            paper = Paper(
                doi=DOI,
                title=TITLE,
                year=2026,
                processing_status="done", review_outcome="approved",
                pdf_storage_path="data/papers/existing.pdf",
            )
            session.add(paper)
            session.commit()
            paper_id = paper.id

        with factory() as session:
            preserved = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=workspace / "data" / "papers",
                workspace=str(workspace),
            )
            assert preserved.status == "existing_with_pdf"
            assert preserved.has_existing_pdf is True
            assert existing_pdf.read_bytes() == b"previous PDF"

        with factory() as session:
            replaced = process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=workspace / "data" / "papers",
                replace_existing=True,
                workspace=str(workspace),
            )
            session.commit()
            saved = session.get(Paper, paper_id)
            assert replaced.status == "replaced"
            assert not existing_pdf.exists()
            assert saved.pdf_storage_path
            assert (workspace / saved.pdf_storage_path).read_bytes().startswith(b"%PDF")
    finally:
        engine.dispose()


def test_pdf_import_reports_archive_copy_failure(tmp_path, monkeypatch):
    from paper_library import pdf_import

    _offline(monkeypatch)
    monkeypatch.setattr(pdf_import.shutil, "copy2", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    source = tmp_path / "copy-failure.pdf"
    _make_pdf(source)
    engine, factory = _engine_and_factory(tmp_path / "copy-failure.db")
    try:
        with factory() as session:
            result = pdf_import.process_pdf_import(
                source,
                session,
                next_id=None,
                pdf_dir=tmp_path / "archive",
                workspace=str(tmp_path),
            )
            assert result.status == "error"
            assert result.error_msg == "PDF 归档失败，请稍后重试"
            assert "disk full" not in result.error_msg
    finally:
        engine.dispose()
