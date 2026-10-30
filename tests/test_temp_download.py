from pathlib import Path
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from api.main import app
from api.temp_download import (
    EXPIRE_SECONDS,
    build_temp_download_url,
    create_temp_file,
    verify_temp_download_url,
)


def test_temp_downloads_remain_available_for_seven_days():
    assert EXPIRE_SECONDS == 7 * 24 * 60 * 60

    before = int(time.time())
    info = create_temp_file(b"shared-content", "shared.pdf", "application/pdf")
    try:
        assert before + EXPIRE_SECONDS <= info["expires_at"] <= before + EXPIRE_SECONDS + 2
    finally:
        Path(info["path"]).unlink(missing_ok=True)


def test_signed_temp_download_url_is_valid_and_tamper_resistant():
    info = create_temp_file(b"test-content", "report.pdf", "application/pdf")
    try:
        url = build_temp_download_url(info)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        expires = int(query["expires"][0])
        signature = query["signature"][0]

        assert parsed.path == f"/api/temp-download/{info['file_id']}"
        assert verify_temp_download_url(info["file_id"], expires, signature)
        assert not verify_temp_download_url(f"{info['file_id']}-changed", expires, signature)
        # Flip the last character to a value guaranteed to differ (a fixed "0" collides 1 time in 16).
        tampered = signature[:-1] + ("1" if signature[-1] == "0" else "0")
        assert not verify_temp_download_url(info["file_id"], expires, tampered)
    finally:
        Path(info["path"]).unlink(missing_ok=True)


def test_expired_temp_download_url_is_rejected():
    info = create_temp_file(b"test-content", "report.docx", "application/octet-stream")
    try:
        expired_info = {**info, "expires_at": 1}
        parsed = urlparse(build_temp_download_url(expired_info))
        query = parse_qs(parsed.query)

        assert not verify_temp_download_url(
            info["file_id"],
            int(query["expires"][0]),
            query["signature"][0],
        )
    finally:
        Path(info["path"]).unlink(missing_ok=True)


def test_temp_download_endpoint_requires_a_valid_signature():
    info = create_temp_file(b"download-me", "report.xlsx", "application/octet-stream")
    client = TestClient(app)
    try:
        signed_url = build_temp_download_url(info)
        response = client.get(signed_url)
        assert response.status_code == 200
        assert response.content == b"download-me"

        separator = "&" if "?" in signed_url else "?"
        invalid = client.get(f"{signed_url}{separator}signature=invalid")
        assert invalid.status_code == 403
    finally:
        Path(info["path"]).unlink(missing_ok=True)


def test_pdf_can_be_previewed_inline_but_other_files_remain_attachments():
    client = TestClient(app)
    pdf = create_temp_file(b"%PDF-test", "paper.pdf", "application/pdf")
    html = create_temp_file(b"<script>alert(1)</script>", "page.html", "text/html")
    try:
        pdf_response = client.get(f"{build_temp_download_url(pdf)}&preview=1")
        assert pdf_response.status_code == 200
        assert pdf_response.headers["content-type"].startswith("application/pdf")
        assert pdf_response.headers["content-disposition"].startswith("inline;")

        html_response = client.get(f"{build_temp_download_url(html)}&preview=1")
        assert html_response.status_code == 200
        assert html_response.headers["content-type"].startswith("application/octet-stream")
        assert html_response.headers["content-disposition"].startswith("attachment;")
    finally:
        Path(pdf["path"]).unlink(missing_ok=True)
        Path(html["path"]).unlink(missing_ok=True)
