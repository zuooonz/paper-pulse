from types import SimpleNamespace

from paper_library import pdf_import


class _Session:
    def __init__(self):
        self.database_lookup_started = False

    def flush(self):
        return None


def test_pdf_import_waits_for_crossref_before_opening_database_transaction(monkeypatch, tmp_path):
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"pdf")
    pdf_dir = tmp_path / "archive"
    session = _Session()
    paper = SimpleNamespace(
        id=3,
        internal_id=7,
        doi="10.1234/example",
        doi_meta=None,
        title="A sufficiently descriptive paper title",
        year=2024,
        journal_name=None,
        pdf_storage_path=None,
        processing_status="done", review_outcome="unreviewed",
    )

    monkeypatch.setattr(pdf_import, "extract_pdf_text", lambda *_args: "paper text")
    monkeypatch.setattr(
        pdf_import, "extract_pdf_metadata", lambda *_args: {"_valid_pdf": True, "title": paper.title}
    )
    monkeypatch.setattr(pdf_import, "extract_doi_from_text", lambda _text: paper.doi)
    monkeypatch.setattr(pdf_import, "normalize_doi", lambda value: value)
    monkeypatch.setattr(pdf_import.time, "sleep", lambda *_args: None)

    def fetch_metadata(_doi, *, structured):
        assert structured is True
        assert session.database_lookup_started is False
        return {"title": paper.title, "year": 2024, "raw": {}}

    def lookup_paper(_session, _doi):
        session.database_lookup_started = True
        return None

    monkeypatch.setattr(pdf_import, "fetch_metadata_by_doi", fetch_metadata)
    monkeypatch.setattr(pdf_import, "get_paper_by_doi", lookup_paper)
    monkeypatch.setattr(pdf_import, "upsert_paper", lambda *_args: paper)
    monkeypatch.setattr(pdf_import, "make_pdf_filename", lambda *_args: "archived.pdf")
    monkeypatch.setattr(pdf_import, "_relative_storage_path", lambda *_args: "archived.pdf")
    monkeypatch.setattr(pdf_import, "get_paper_image_dir", lambda *_args: tmp_path / "images")
    monkeypatch.setattr(pdf_import, "extract_text_and_images", lambda *_args: ("text", []))
    monkeypatch.setattr(pdf_import, "refresh_pdf_flag", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(pdf_import, "refresh_metadata_complete", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(pdf_import, "enrich_missing_abstract", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pdf_import, "_get_jcr_matcher", lambda *_args: None)

    result = pdf_import.process_pdf_import(
        pdf_path,
        session,
        next_id=None,
        pdf_dir=pdf_dir,
        skip_title_check=True,
        workspace=tmp_path,
    )

    assert session.database_lookup_started is True
    assert result.status == "created"
    assert (pdf_dir / "archived.pdf").exists()
