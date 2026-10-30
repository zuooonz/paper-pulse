"""Registry of durable job handlers."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

Handler = Callable[[dict[str, Any], str], Any]


class PermanentJobError(RuntimeError):
    """An expected business failure that must not repeat external side effects."""


def _required_value(payload: dict[str, Any], key: str) -> Any:
    if not isinstance(payload, dict) or key not in payload or payload[key] is None:
        raise PermanentJobError(f"Invalid job payload: missing {key}")
    return payload[key]


def _publish_draft(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from api.config import settings
    from paper_library.db import get_session
    from paper_library.models import PublishRecord
    from api.services.publishing import execute_publish_record

    try:
        record_id = int(_required_value(payload, "record_id"))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("Invalid publish job payload: record_id must be an integer") from exc
    workspace = payload.get("workspace") or settings.workspace
    config_dir = payload.get("config_dir") or settings.config_dir
    with get_session(workspace, config_dir) as session:
        record = session.get(PublishRecord, record_id)
        if record is None:
            return {"record_id": record_id, "status": "missing"}
        if record.status in {"success", "failed", "cancelled"}:
            result = {"record_id": record_id, "status": record.status, "error": record.error, "url": record.url}
            if record.status == "failed":
                raise PermanentJobError(record.error or "Draft publishing failed")
            return result
    result = execute_publish_record(record_id, workspace, config_dir)
    if result.get("status") == "failed":
        raise PermanentJobError(result.get("error") or "Draft publishing failed")
    return result


def _publish_video(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from api.routers.videos import execute_video_publish

    try:
        int(_required_value(payload, "record_id"))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("Invalid video publish payload: record_id must be an integer") from exc
    result = execute_video_publish(payload)
    if result.get("status") == "failed":
        raise PermanentJobError(result.get("error") or "Video publishing failed")
    return result


def _literature_search(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    from api.routers.literature_search import run_literature_search
    from api.routers.literature_search import LiteratureSearchRequest

    try:
        LiteratureSearchRequest.model_validate(_required_value(payload, "request"))
    except (ValidationError, TypeError, ValueError) as exc:
        raise PermanentJobError("Invalid literature search request") from exc
    return run_literature_search(job_id, payload)


def _poll_download(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from api.routers.papers import poll_generated_video_download

    _required_value(payload, "task_id")
    _required_value(payload, "video_id")
    result = poll_generated_video_download(payload)
    if result.get("status") in {"failed", "timeout"}:
        raise PermanentJobError(f"Generated video task {result['status']}")
    return result


def _summary_job(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from api.summary_jobs import run_summary_job

    return run_summary_job(str(_required_value(payload, "summary_job_id")))


def _cookie_validation(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from api.scheduler import _run_cookie_validation

    try:
        workspace = _required_value(payload, "workspace")
        config_dir = _required_value(payload, "config_dir")
    except PermanentJobError:
        raise
    return _run_cookie_validation(workspace, config_dir)


def _reconcile_pdf_flags(payload: dict[str, Any], _job_id: str) -> dict[str, Any]:
    from paper_library.db import get_session
    from paper_library.paper_lifecycle import reconcile_pdf_flags

    try:
        workspace = _required_value(payload, "workspace")
        config_dir = _required_value(payload, "config_dir")
    except PermanentJobError:
        raise
    with get_session(workspace, config_dir) as session:
        corrected = reconcile_pdf_flags(session, workspace)
    return {"corrected": corrected}


HANDLERS: dict[str, Handler] = {
    "publish_draft": _publish_draft,
    "publish_video": _publish_video,
    "literature_search": _literature_search,
    "poll_download": _poll_download,
    "summary_job": _summary_job,
    "cookie_validation": _cookie_validation,
    "reconcile_pdf_flags": _reconcile_pdf_flags,
}


def register(kind: str, handler: Handler) -> None:
    """Register or replace a handler, primarily for extensions and tests."""
    HANDLERS[kind] = handler


def get_handler(kind: str) -> Handler:
    try:
        return HANDLERS[kind]
    except KeyError as exc:
        raise LookupError(f"No worker handler registered for job kind: {kind}") from exc
