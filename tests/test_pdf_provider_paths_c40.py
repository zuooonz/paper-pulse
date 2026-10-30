"""Offline coverage for downloader providers and their URL safety boundary."""

from __future__ import annotations

import json

import pymupdf
import pytest


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            doc.new_page()
        return doc.tobytes()
    finally:
        doc.close()


class FakeResponse:
    def __init__(self, *, status=200, content=b"%PDF- synthetic", payload=None, headers=None, text="", url=""):
        self.status_code = status
        self.content = content
        self.payload = payload or {}
        self.headers = headers or {"Content-Type": "application/pdf"}
        self.text = text
        self.url = url
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


def _downloader(**kwargs):
    from paper_library.pdf_downloader import PdfDownloader

    return PdfDownloader(request_interval=0, **kwargs)


@pytest.mark.parametrize(
    ("provider", "responses", "kwargs", "prepare"),
    [
        (
            "_try_europepmc",
            [
                FakeResponse(payload={"resultList": {"result": [{"pmcid": "PMC123", "hasPdf": True}]}}),
                FakeResponse(),
            ],
            {"api_endpoints": {"europe_pmc_base_url": "https://europe.example", "europe_pmc_article_base_url": "https://articles.example"}},
            None,
        ),
        (
            "_try_unpaywall",
            [
                FakeResponse(payload={"best_oa_location": {"url_for_pdf": "https://files.example/paper", "url": "https://journal.example/article"}}),
                FakeResponse(),
            ],
            {"unpaywall_email": "test@example.invalid", "api_endpoints": {"unpaywall_base_url": "https://unpaywall.example"}},
            None,
        ),
        (
            "_try_semantic_scholar",
            [
                FakeResponse(payload={"openAccessPdf": {"url": "https://files.example/paper"}}),
                FakeResponse(),
            ],
            {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "https://s2.example"}},
            None,
        ),
        (
            "_try_arxiv",
            [
                FakeResponse(payload={"externalIds": {"ArXiv": "2601.01234"}}),
                FakeResponse(),
            ],
            {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "https://s2.example", "arxiv_base_url": "https://arxiv.example/pdf"}},
            None,
        ),
        (
            "_try_springer",
            [FakeResponse()],
            {"api_endpoints": {"springer_base_url": "https://springer.example/content"}},
            None,
        ),
        (
            "_try_wiley",
            [FakeResponse()],
            {"wiley_tdm_token": "configured", "api_endpoints": {"wiley_base_url": "https://wiley.example"}},
            None,
        ),
        (
            "_try_elsevier",
            [FakeResponse(content=_pdf_bytes(), headers={"Content-Type": "application/pdf"})],
            {"elsevier_api_key": "configured", "api_endpoints": {"elsevier_base_url": "https://elsevier.example"}},
            None,
        ),
        (
            "_try_mdpi_cdn",
            [
                FakeResponse(url="https://www.mdpi.com/1234-5678/4/2/17"),
                FakeResponse(),
            ],
            {"api_endpoints": {"doi_resolver_base_url": "https://doi.example", "mdpi_base_url": "https://cdn.example"}},
            "mdpi",
        ),
        (
            "_try_ieee",
            [
                FakeResponse(payload={"message": {"link": [{"URL": "https://ieee.example/document?arnumber=123456"}]}}),
                FakeResponse(),
                FakeResponse(text='<iframe src="https://files.example/document.pdf?arnumber=123456"></iframe>'),
                FakeResponse(),
            ],
            {"api_endpoints": {"crossref_base_url": "https://crossref.example", "ieee_xplore_base_url": "https://ieee.example"}},
            None,
        ),
    ],
)
def test_each_provider_accepts_a_synthetic_pdf(
    tmp_path, monkeypatch, provider, responses, kwargs, prepare
):
    from paper_library import pdf_downloader

    if provider == "_try_mdpi_cdn":
        pytest.importorskip("curl_cffi")

    calls = []

    def fake_get(_session, url, **request_kwargs):
        calls.append((url, request_kwargs))
        response = responses.pop(0)
        return response

    monkeypatch.setattr(pdf_downloader, "safe_get", fake_get)
    downloader = _downloader(**kwargs)
    save_path = tmp_path / "downloads" / "paper.pdf"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    if prepare == "mdpi":
        (tmp_path / "mdpi_issn_journal_map.json").write_text(
            json.dumps({"1234-5678": "synthetic"}), encoding="utf-8"
        )

    doi = "10.3390/synthetic00000017" if provider == "_try_mdpi_cdn" else "10.5555/synthetic.2026"
    result = getattr(downloader, provider)(doi, save_path)

    assert result.success is True
    assert result.path == str(save_path)
    assert save_path.read_bytes().startswith(b"%PDF")
    assert calls


@pytest.mark.parametrize(
    ("provider", "kwargs"),
    [
        ("_try_europepmc", {"api_endpoints": {"europe_pmc_base_url": "https://europe.example", "europe_pmc_article_base_url": "https://articles.example"}}),
        ("_try_unpaywall", {"unpaywall_email": "test@example.invalid", "api_endpoints": {"unpaywall_base_url": "https://unpaywall.example"}}),
        ("_try_semantic_scholar", {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "https://s2.example"}}),
        ("_try_arxiv", {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "https://s2.example", "arxiv_base_url": "https://arxiv.example"}}),
        ("_try_springer", {"api_endpoints": {"springer_base_url": "https://springer.example"}}),
        ("_try_wiley", {"wiley_tdm_token": "configured", "api_endpoints": {"wiley_base_url": "https://wiley.example"}}),
        ("_try_elsevier", {"elsevier_api_key": "configured", "api_endpoints": {"elsevier_base_url": "https://elsevier.example"}}),
        ("_try_mdpi_cdn", {"api_endpoints": {"doi_resolver_base_url": "https://doi.example", "mdpi_base_url": "https://cdn.example"}}),
        ("_try_ieee", {"api_endpoints": {"crossref_base_url": "https://crossref.example", "ieee_xplore_base_url": "https://ieee.example"}}),
    ],
)
def test_provider_returns_error_for_non_pdf_payload(tmp_path, monkeypatch, provider, kwargs):
    from paper_library import pdf_downloader

    if provider == "_try_mdpi_cdn":
        pytest.importorskip("curl_cffi")

    def fake_get(_session, _url, **_request_kwargs):
        if provider in {"_try_europepmc", "_try_unpaywall", "_try_semantic_scholar", "_try_arxiv", "_try_mdpi_cdn", "_try_ieee"}:
            # Supply provider metadata responses first, then an HTML body for the PDF request.
            if not getattr(fake_get, "metadata_sent", False):
                fake_get.metadata_sent = True
                if provider == "_try_europepmc":
                    return FakeResponse(payload={"resultList": {"result": [{"pmcid": "PMC123", "hasPdf": True}]}})
                if provider == "_try_unpaywall":
                    return FakeResponse(payload={"best_oa_location": {"url_for_pdf": "https://files.example/paper", "url": "https://journal.example/article"}})
                if provider == "_try_semantic_scholar":
                    return FakeResponse(payload={"openAccessPdf": {"url": "https://files.example/paper"}})
                if provider == "_try_arxiv":
                    return FakeResponse(payload={"externalIds": {"ArXiv": "2601.01234"}})
                if provider == "_try_mdpi_cdn":
                    return FakeResponse(url="https://www.mdpi.com/1234-5678/4/2/17")
                if provider == "_try_ieee":
                    return FakeResponse(payload={"message": {"link": [{"URL": "https://ieee.example/document?arnumber=123456"}]}})
            if provider == "_try_ieee" and not getattr(fake_get, "stamp_sent", False):
                fake_get.stamp_sent = True
                return FakeResponse(text='<iframe src="https://files.example/document.pdf"></iframe>')
        return FakeResponse(content=b"<html>not a PDF</html>", headers={"Content-Type": "text/html"})

    monkeypatch.setattr(pdf_downloader, "safe_get", fake_get)
    downloader = _downloader(**kwargs)
    save_path = tmp_path / "downloads" / "paper.pdf"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    if provider == "_try_mdpi_cdn":
        (tmp_path / "mdpi_issn_journal_map.json").write_text(json.dumps({"1234-5678": "synthetic"}), encoding="utf-8")

    result = getattr(downloader, provider)("10.3390/synthetic00000017" if provider == "_try_mdpi_cdn" else "10.5555/synthetic.2026", save_path)

    assert result.success is False
    assert not save_path.exists()


def test_mdpi_missing_curl_cffi_returns_actionable_failure(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    downloader = _downloader(api_endpoints={
        "doi_resolver_base_url": "https://doi.example",
        "mdpi_base_url": "https://cdn.example",
    })

    result = downloader._try_mdpi_cdn("10.3390/synthetic00000017", tmp_path / "paper.pdf")

    assert result.success is False
    assert "paper-pulse[pdf]" in result.error
    assert not (tmp_path / "paper.pdf").exists()


def test_mdpi_missing_curl_cffi_continues_to_next_provider(tmp_path, monkeypatch):
    import sys

    from paper_library.pdf_downloader import DownloadResult

    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    downloader = _downloader(api_endpoints={
        "doi_resolver_base_url": "https://doi.example",
        "mdpi_base_url": "https://cdn.example",
    })
    later_provider_calls = []

    def springer_success(doi, save_path):
        later_provider_calls.append(doi)
        return DownloadResult(success=True, path=str(save_path), method="Springer Direct")

    monkeypatch.setattr(downloader, "_try_springer", springer_success)
    save_path = tmp_path / "paper.pdf"
    result = downloader.download_by_doi(
        "10.3390/synthetic00000017",
        save_path,
        platforms=["mdpi", "springer"],
    )

    assert result.success is True
    assert result.method == "Springer Direct"
    assert later_provider_calls == ["10.3390/synthetic00000017"]


@pytest.mark.parametrize(
    ("provider", "kwargs", "doi"),
    [
        ("_try_europepmc", {"api_endpoints": {"europe_pmc_base_url": "http://127.0.0.1", "europe_pmc_article_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_unpaywall", {"unpaywall_email": "test@example.invalid", "api_endpoints": {"unpaywall_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_semantic_scholar", {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_arxiv", {"semantic_scholar_api_key": "configured", "api_endpoints": {"semantic_scholar_base_url": "http://127.0.0.1", "arxiv_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_springer", {"api_endpoints": {"springer_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_wiley", {"wiley_tdm_token": "configured", "api_endpoints": {"wiley_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_elsevier", {"elsevier_api_key": "configured", "api_endpoints": {"elsevier_base_url": "http://127.0.0.1"}}, "10.5555/x"),
        ("_try_mdpi_cdn", {"api_endpoints": {"doi_resolver_base_url": "http://127.0.0.1", "mdpi_base_url": "http://127.0.0.1"}}, "10.3390/x"),
        ("_try_ieee", {"api_endpoints": {"crossref_base_url": "http://127.0.0.1", "ieee_xplore_base_url": "http://127.0.0.1"}}, "10.5555/x"),
    ],
)
def test_each_provider_stops_at_ssrf_block_before_network_io(tmp_path, provider, kwargs, doi, monkeypatch):

    downloader = _downloader(**kwargs)

    def forbidden_network(*_args, **_kwargs):
        pytest.fail("The downloader must reject the loopback URL before network access")

    monkeypatch.setattr(downloader._session, "get", forbidden_network)
    save_path = tmp_path / "paper.pdf"
    result = getattr(downloader, provider)(doi, save_path)
    assert result.success is False
    assert isinstance(result.error, str)
    assert not save_path.exists()


def test_europepmc_redirect_is_checked_and_non_pdf_response_is_rejected(tmp_path, monkeypatch):
    from core import net_safety

    monkeypatch.setattr(net_safety, "validate_download_url", lambda url: url)
    responses = [
        FakeResponse(status=302, headers={"Location": "https://europe.example/search-final"}),
        FakeResponse(payload={"resultList": {"result": [{"pmcid": "PMC123", "hasPdf": True}]}}),
        FakeResponse(content=b"<html>login page</html>", headers={"Content-Type": "text/html"}),
    ]

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return responses.pop(0)

    downloader = _downloader(api_endpoints={
        "europe_pmc_base_url": "https://europe.example",
        "europe_pmc_article_base_url": "https://articles.example",
    })
    fake_session = FakeSession()
    downloader._session = fake_session
    result = downloader._try_europepmc("10.5555/synthetic", tmp_path / "paper.pdf")

    assert result.success is False
    assert len(fake_session.calls) == 3
    assert fake_session.calls[0][1]["allow_redirects"] is False
    assert fake_session.calls[1][0] == "https://europe.example/search-final"
    assert not (tmp_path / "paper.pdf").exists()


def test_provider_redirect_to_loopback_is_rejected_before_following(tmp_path, monkeypatch):
    from core.net_safety import UnsafeDownloadURL

    def validate(url):
        if "127.0.0.1" in url:
            raise UnsafeDownloadURL("loopback blocked")
        return url

    monkeypatch.setattr("core.net_safety.validate_download_url", validate)
    response = FakeResponse(status=302, headers={"Location": "http://127.0.0.1/private.pdf"})

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return response

    downloader = _downloader(api_endpoints={
        "springer_base_url": "https://springer.example/content",
    })
    fake_session = FakeSession()
    downloader._session = fake_session
    result = downloader._try_springer("10.5555/synthetic", tmp_path / "paper.pdf")

    assert result.success is False
    assert "loopback blocked" in result.error
    assert len(fake_session.calls) == 1
    assert response.closed is True


def test_invalid_doi_and_platform_whitelist_skip_unselected_sources(monkeypatch, tmp_path):
    downloader = _downloader()
    assert downloader.download_by_doi("not-a-doi", tmp_path / "bad.pdf").success is False

    attempted = []
    monkeypatch.setattr(downloader, "_try_europepmc", lambda *_: attempted.append("europepmc"))
    monkeypatch.setattr(downloader, "_try_unpaywall", lambda *_: attempted.append("unpaywall"))
    result = downloader.download_by_doi("10.5555/synthetic", tmp_path / "none.pdf", platforms=["unrecognized"])
    assert result.success is False
    assert attempted == []
