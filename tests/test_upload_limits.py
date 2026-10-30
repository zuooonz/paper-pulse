import runpy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import upload_limits
from tools.upload_limits import (
    PDF_MAX_UPLOAD_BYTES,
    UploadTooLarge,
    copy_upload,
    copy_upload_to_path,
    validate_upload_size,
)
from api.routers.papers.schemas import BatchDoiRequest, BatchDownloadBody, BatchGenerateDraftRequest
from api.routers.papers.common import _parse_ids
from api.routers.drafts import BatchPublishRequest, BatchScheduleRequest
from api.routers.videos import MAX_VIDEO_BATCH_ITEMS, _validate_id_count
from api.routers import videos
from api.routers.papers import ingest
from fastapi import HTTPException
from api.services.draft_uploads import DraftUploadError, upload_word


UPLOAD_LIMIT_DEFAULTS = {
    "PDF_MAX_UPLOAD_MB": 100,
    "WORD_MAX_UPLOAD_MB": 50,
    "VIDEO_MAX_UPLOAD_MB": 2048,
    "COVER_MAX_UPLOAD_MB": 20,
}


def _load_upload_limits():
    return runpy.run_path(Path(upload_limits.__file__))


def test_upload_limit_environment_defaults(monkeypatch):
    for name in UPLOAD_LIMIT_DEFAULTS:
        monkeypatch.delenv(name, raising=False)

    configured = _load_upload_limits()

    for name, default_mb in UPLOAD_LIMIT_DEFAULTS.items():
        assert configured[name] == default_mb
        assert configured[name.replace("_MB", "_BYTES")] == default_mb * 1024 * 1024


def test_upload_limit_environment_overrides(monkeypatch):
    overrides = {
        "PDF_MAX_UPLOAD_MB": 120,
        "WORD_MAX_UPLOAD_MB": 64,
        "VIDEO_MAX_UPLOAD_MB": 3072,
        "COVER_MAX_UPLOAD_MB": 32,
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, str(value))

    configured = _load_upload_limits()

    for name, value in overrides.items():
        assert configured[name] == value
        assert configured[name.replace("_MB", "_BYTES")] == value * 1024 * 1024


def test_invalid_upload_limit_environment_values_warn_and_fall_back(monkeypatch, caplog):
    invalid_values = {
        "PDF_MAX_UPLOAD_MB": "not-a-number",
        "WORD_MAX_UPLOAD_MB": "0",
        "VIDEO_MAX_UPLOAD_MB": "-1",
        "COVER_MAX_UPLOAD_MB": "2.5",
    }
    for name, value in invalid_values.items():
        monkeypatch.setenv(name, value)

    with caplog.at_level("WARNING"):
        configured = _load_upload_limits()

    for name, default_mb in UPLOAD_LIMIT_DEFAULTS.items():
        assert configured[name] == default_mb
    for name in invalid_values:
        assert f"Invalid {name}" in caplog.text


def test_upload_size_is_checked_without_consuming_stream():
    upload = SimpleNamespace(file=BytesIO(b"12345"), size=5)

    with pytest.raises(UploadTooLarge):
        validate_upload_size(upload, 4, label="PDF")

    assert upload.file.tell() == 0


def test_upload_copy_is_chunked_and_enforces_unknown_stream_size():
    source = BytesIO(b"abcdefgh")
    destination = BytesIO()

    with pytest.raises(UploadTooLarge):
        copy_upload(source, destination, 5, chunk_bytes=3)

    assert destination.getvalue() == b"abc"


def test_oversized_upload_removes_partial_file(tmp_path):
    path = tmp_path / "partial.pdf"

    with pytest.raises(UploadTooLarge):
        copy_upload_to_path(BytesIO(b"too long"), path, 3)

    assert not path.exists()


def test_batch_request_models_enforce_item_caps():
    with pytest.raises(ValueError):
        BatchDoiRequest(dois=[f"10.1000/{index}" for index in range(101)])
    with pytest.raises(ValueError):
        BatchGenerateDraftRequest(paper_ids=list(range(101)))
    with pytest.raises(ValueError):
        BatchDownloadBody(paper_ids=list(range(201)))
    with pytest.raises(ValueError):
        BatchPublishRequest(draft_ids=list(range(101)))
    with pytest.raises(ValueError):
        BatchScheduleRequest(draft_ids=list(range(101)), start_at="2026-01-01T00:00:00Z")


def test_video_batch_ids_enforce_count_and_uniqueness():
    with pytest.raises(HTTPException) as too_many:
        _validate_id_count(list(range(MAX_VIDEO_BATCH_ITEMS + 1)), MAX_VIDEO_BATCH_ITEMS, "视频")
    assert too_many.value.status_code == 422

    with pytest.raises(HTTPException) as duplicates:
        _validate_id_count([1, 1], MAX_VIDEO_BATCH_ITEMS, "视频")
    assert duplicates.value.status_code == 422


def test_word_upload_rejects_oversized_file_before_writing(tmp_path):
    from tools.upload_limits import WORD_MAX_UPLOAD_BYTES

    upload = SimpleNamespace(filename="large.docx", size=WORD_MAX_UPLOAD_BYTES + 1, file=BytesIO())
    with pytest.raises(DraftUploadError) as error:
        upload_word(upload, workspace=str(tmp_path), config_dir="config")

    assert error.value.status_code == 413
    assert list(tmp_path.rglob("*.docx")) == []


def test_pdf_routes_reject_oversized_upload_before_database_access(monkeypatch):
    upload = SimpleNamespace(
        filename="large.pdf",
        size=PDF_MAX_UPLOAD_BYTES + 1,
        file=BytesIO(),
    )
    monkeypatch.setattr(ingest, "get_session", lambda **_kwargs: pytest.fail("DB should not be opened"))

    with pytest.raises(HTTPException) as batch_error:
        ingest.batch_pdf_upload([upload], user={"role": "uploader"})
    assert batch_error.value.status_code == 413

    with pytest.raises(HTTPException) as single_error:
        ingest.upload_pdf(1, upload, user={"role": "uploader"})
    assert single_error.value.status_code == 413


def test_pdf_batch_rejects_too_many_files_before_database_access(monkeypatch):
    uploads = [SimpleNamespace(filename=f"{index}.pdf", size=1, file=BytesIO(b"x")) for index in range(11)]
    monkeypatch.setattr(ingest, "get_session", lambda **_kwargs: pytest.fail("DB should not be opened"))

    with pytest.raises(HTTPException) as error:
        ingest.batch_pdf_upload(uploads, user={"role": "uploader"})

    assert error.value.status_code == 413


def test_comma_separated_paper_filter_has_item_limit():
    assert _parse_ids("1, 2,3") == [1, 2, 3]
    with pytest.raises(HTTPException) as error:
        _parse_ids(",".join(str(index) for index in range(201)))
    assert error.value.status_code == 422


def test_video_upload_rejects_oversized_file_before_storage_access(monkeypatch):
    from tools.upload_limits import VIDEO_MAX_UPLOAD_BYTES

    upload = SimpleNamespace(
        filename="large.mp4",
        size=VIDEO_MAX_UPLOAD_BYTES + 1,
        file=BytesIO(),
    )
    monkeypatch.setattr(videos, "_video_dir", lambda: pytest.fail("storage should not be accessed"))

    with pytest.raises(HTTPException) as error:
        videos.upload_video(title="large", file=upload, user={"role": "reviewer"})

    assert error.value.status_code == 413
