import pytest

from core import net_safety
from core.net_safety import UnsafeDownloadURL, safe_get, validate_download_url


def test_url_validation_allows_public_ip_and_rejects_private_ip(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_NETWORK_DOWNLOADS", raising=False)

    assert validate_download_url("https://1.1.1.1/paper.pdf") == "https://1.1.1.1/paper.pdf"
    with pytest.raises(UnsafeDownloadURL, match="非公网 IP"):
        validate_download_url("http://127.0.0.1/private")

    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_DOWNLOADS", "true")
    assert validate_download_url("http://127.0.0.1/private") == "http://127.0.0.1/private"


@pytest.mark.parametrize("url", ["file:///tmp/paper.pdf", "ftp://example.com/paper.pdf", "https:///missing-host"])
def test_url_validation_rejects_non_http_or_malformed_urls(url):
    with pytest.raises(UnsafeDownloadURL):
        validate_download_url(url)


def test_url_validation_rejects_a_hostname_with_any_private_dns_result(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_NETWORK_DOWNLOADS", raising=False)
    monkeypatch.setattr(
        net_safety.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("10.0.0.8", 443)),
        ],
    )

    with pytest.raises(UnsafeDownloadURL, match="非公网 IP"):
        validate_download_url("https://papers.example/paper.pdf")


def test_safe_get_checks_redirect_target_before_requesting_it(monkeypatch):
    checked = []
    monkeypatch.setattr(
        net_safety,
        "validate_download_url",
        lambda url: checked.append(url) or (
            (_ for _ in ()).throw(UnsafeDownloadURL("blocked")) if "private" in url else url
        ),
    )

    class FakeResponse:
        status_code = 302
        headers = {"Location": "http://private.example/resource"}

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.response = FakeResponse()

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.response

    session = FakeSession()
    with pytest.raises(UnsafeDownloadURL, match="blocked"):
        safe_get(session, "https://public.example/start")

    assert [call[0] for call in session.calls] == ["https://public.example/start"]
    assert "http://private.example/resource" in checked
    assert session.response.closed is True
    assert session.calls[0][1]["allow_redirects"] is False


def test_safe_get_follows_redirects_with_a_five_hop_limit(monkeypatch):
    monkeypatch.setattr(net_safety, "validate_download_url", lambda url: url)

    class FakeResponse:
        headers = {"Location": "/next"}

        def __init__(self, status_code):
            self.status_code = status_code
            self.closed = False

        def close(self):
            self.closed = True

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.responses = [FakeResponse(302) for _ in range(6)]

        def get(self, url, **_kwargs):
            self.calls.append(url)
            return self.responses[len(self.calls) - 1]

    session = FakeSession()
    with pytest.raises(UnsafeDownloadURL, match="重定向次数"):
        safe_get(session, "https://public.example/start", max_redirects=5)
    assert len(session.calls) == 6
    assert all(response.closed for response in session.responses)


def test_safe_get_drops_sensitive_headers_on_cross_origin_redirect(monkeypatch):
    monkeypatch.setattr(net_safety, "validate_download_url", lambda url: url)

    class FakeResponse:
        def __init__(self, status_code, location=None):
            self.status_code = status_code
            self.headers = {"Location": location} if location else {}

        def close(self):
            pass

    class FakeSession:
        def __init__(self):
            self.calls = []
            self.responses = [
                FakeResponse(302, "https://other.example/file"),
                FakeResponse(200),
            ]

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.responses[len(self.calls) - 1]

    session = FakeSession()
    result = safe_get(
        session,
        "https://public.example/start",
        headers={"Authorization": "secret", "User-Agent": "test"},
    )

    assert result.status_code == 200
    assert session.calls[1][1]["headers"] == {"User-Agent": "test"}
