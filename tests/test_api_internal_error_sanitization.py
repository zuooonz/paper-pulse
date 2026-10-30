import logging
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import literature_search, videos
from api.routers.papers import ingest


class _Session:
    def __init__(self, paper):
        self.paper = paper

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _model, _paper_id):
        return self.paper

    def commit(self):
        return None

    def expire(self, *_args, **_kwargs):
        return None


@pytest.mark.parametrize("failure", ["extract", "llm"])
def test_pdf_upload_error_responses_do_not_expose_exception_text(
    monkeypatch, tmp_path, failure, caplog
):
    secret_detail = "database password at /private/path"
    paper = SimpleNamespace(
        id=7,
        internal_id=12,
        year=2024,
        title="A paper title",
        pdf_storage_path=None,
        updated_at=None,
        extracted_text=None,
        extracted_images=None,
        pdf_text_extracted=False,
    )
    session = _Session(paper)
    upload = SimpleNamespace(filename="paper.pdf", size=4, file=BytesIO(b"%PDF"))

    monkeypatch.setattr(ingest, "get_session", lambda **_kwargs: session)
    monkeypatch.setattr(ingest.storage, "get_pdf_dir", lambda _workspace: tmp_path)
    monkeypatch.setattr(ingest.storage, "ensure_relative_path", lambda _path, _ws: "paper.pdf")
    monkeypatch.setattr(ingest.storage, "get_paper_image_dir", lambda *_args: tmp_path / "images")
    monkeypatch.setattr(ingest, "complete_processing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingest, "fail_processing", lambda *_args, **_kwargs: None)

    if failure == "extract":
        def fail_extract(*_args, **_kwargs):
            raise RuntimeError(secret_detail)

        monkeypatch.setattr(ingest, "extract_text_and_images", fail_extract)
    else:
        monkeypatch.setattr(ingest, "extract_text_and_images", lambda *_args, **_kwargs: ("text", []))

        class FailingConfigLoader:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError(secret_detail)

        monkeypatch.setattr("core.config_loader.ConfigLoader", FailingConfigLoader)

    with caplog.at_level(logging.ERROR):
        response = ingest.upload_pdf(7, upload, user={"role": "uploader"})

    assert secret_detail not in response["message"]
    assert "失败" in response["message"]
    assert secret_detail in caplog.text


def test_video_generation_fallback_logs_exception_without_returning_it(caplog):
    secret_detail = "provider token at /private/config.yaml"
    try:
        raise RuntimeError(secret_detail)
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR):
            result = videos._video_generation_fallback("标题", "摘要", "标签", exc)

    assert secret_detail not in result["script"]
    assert "视频脚本生成暂时失败" in result["script"]
    assert secret_detail in caplog.text


def test_literature_search_start_missing_persisted_job_returns_service_unavailable(monkeypatch):
    import worker.queue

    monkeypatch.setattr(worker.queue, "enqueue_exclusive", lambda *_args, **_kwargs: ("task-id", True))
    monkeypatch.setattr(worker.queue, "get_job", lambda _task_id: None)

    with pytest.raises(HTTPException) as error:
        literature_search.start_literature_search(
            literature_search.LiteratureSearchRequest(), user={"role": "uploader", "username": "tester"}
        )

    assert error.value.status_code == 503
    assert error.value.detail == "检索服务暂不可用，请稍后重试"
