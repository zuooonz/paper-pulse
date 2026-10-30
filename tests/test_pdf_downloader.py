"""Regression tests for PDF download validation helpers."""

from pathlib import Path

import pymupdf

from paper_library.models import Paper
from paper_library.pdf_downloader import DownloadResult, PdfDownloader
from paper_library.paper_lifecycle import refresh_pdf_flag


def _write_pdf(path: Path, pages: int) -> None:
    document = pymupdf.open()
    try:
        for _ in range(pages):
            document.new_page()
        document.save(path)
    finally:
        document.close()


def test_small_single_page_pdf_is_treated_as_truncated(tmp_path):
    pdf_path = tmp_path / "single-page.pdf"
    _write_pdf(pdf_path, pages=1)

    assert PdfDownloader._is_truncated_pdf(pdf_path) is True


def test_multi_page_pdf_is_not_treated_as_truncated(tmp_path):
    pdf_path = tmp_path / "multi-page.pdf"
    _write_pdf(pdf_path, pages=2)

    assert PdfDownloader._is_truncated_pdf(pdf_path) is False


def test_invalid_pdf_is_not_classified_as_truncated(tmp_path):
    pdf_path = tmp_path / "invalid.pdf"
    pdf_path.write_bytes(b"not a pdf")

    assert PdfDownloader._is_truncated_pdf(pdf_path) is False


def test_all_pdf_sources_failed_only_records_download_error(tmp_path, monkeypatch):
    downloader = PdfDownloader(
        elsevier_api_key="configured",
        semantic_scholar_api_key="configured",
        wiley_tdm_token="configured",
        request_interval=0,
    )

    def fail_source(*_args, **_kwargs):
        return DownloadResult(error="provider response with details")

    for source in (
        "_try_europepmc",
        "_try_unpaywall",
        "_try_semantic_scholar",
        "_try_arxiv",
        "_try_springer",
        "_try_wiley",
        "_try_elsevier",
        "_try_ieee",
    ):
        monkeypatch.setattr(downloader, source, fail_source)

    paper = Paper(doi="10.1000/test", processing_status="pending", review_outcome="unreviewed")
    result = downloader.download_for_paper(paper, session=None, workspace=str(tmp_path))

    assert result.success is False
    assert paper.processing_status == "pending"
    assert paper.review_outcome == "unreviewed"
    assert paper.processing_error == "pdf_download_failed"


def test_pdf_download_failure_preserves_unreviewed(tmp_path, monkeypatch):
    downloader = PdfDownloader(request_interval=0)
    monkeypatch.setattr(
        downloader,
        "download_by_doi",
        lambda *_args, **_kwargs: DownloadResult(error="private provider details"),
    )
    paper = Paper(doi="10.1000/test", processing_status="done", review_outcome="unreviewed")

    downloader.download_for_paper(paper, session=None, workspace=str(tmp_path))

    assert paper.review_outcome == "unreviewed"
    assert paper.processing_error == "pdf_download_failed"


def test_download_failure_keeps_completed_review_state(tmp_path, monkeypatch):
    downloader = PdfDownloader(request_interval=0)
    monkeypatch.setattr(
        downloader,
        "download_by_doi",
        lambda *_args, **_kwargs: DownloadResult(error="private provider details"),
    )
    paper = Paper(
        doi="10.1000/test", processing_status="done", review_outcome="approved",
        pdf_storage_path="missing.pdf", has_pdf=True,
    )

    downloader.download_for_paper(paper, session=None, workspace=str(tmp_path))

    assert paper.processing_status == "done"
    assert paper.review_outcome == "approved"
    assert paper.processing_error == "pdf_download_failed"
    assert paper.has_pdf is False


def test_pdf_flag_refresh_tracks_file_write_and_delete(tmp_path):
    pdf = tmp_path / "paper.pdf"
    paper = Paper(pdf_storage_path="paper.pdf")
    assert refresh_pdf_flag(paper, tmp_path) is False
    pdf.write_bytes(b"%PDF-test")
    assert refresh_pdf_flag(paper, tmp_path) is True
    pdf.unlink()
    assert refresh_pdf_flag(paper, tmp_path) is False
