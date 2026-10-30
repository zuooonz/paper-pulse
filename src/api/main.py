"""FastAPI application entry point for the research content API."""
import logging
import sys
import re
import time
import uuid
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .routers import auth, drafts, accounts, stats, papers, auto_login, authors, videos, dingtalk_auth, dingtalk_admin, literature_search, governance, config_options

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure src/ is importable, then initialise the DB
    src_dir = str(Path(__file__).resolve().parent.parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from .config import settings
    from .dependencies import validate_auth_configuration, warn_if_plaintext_passwords_enabled
    from publisher.credential_store import warn_if_encryption_key_missing
    from paper_library.db import init_db
    from .scheduler import start_scheduler, stop_scheduler

    validate_auth_configuration()
    warn_if_plaintext_passwords_enabled()
    warn_if_encryption_key_missing()
    init_db(workspace=settings.workspace, config_dir=settings.config_dir)
    if os.environ.get("RUN_SCHEDULER_IN_API", "false").strip().lower() in {"1", "true", "yes", "on"}:
        start_scheduler(settings.workspace, settings.config_dir)
    yield
    if os.environ.get("RUN_SCHEDULER_IN_API", "false").strip().lower() in {"1", "true", "yes", "on"}:
        stop_scheduler()


app = FastAPI(title="Research Content API", version="1.0.0", lifespan=lifespan)


@app.get("/healthz", include_in_schema=False)
def healthz():
    from .config import settings
    from paper_library.db import get_session

    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok"}

cors_config = os.getenv("CORS_ORIGINS")
cors_origins = [origin.strip() for origin in cors_config.split(",") if origin.strip()] if cors_config else []
if not cors_origins:
    from core.service_config import runtime_config

    configured_origins = runtime_config().get("cors_origins") or [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    if isinstance(configured_origins, str):
        configured_origins = configured_origins.split(",")
    cors_origins = [str(origin).strip() for origin in configured_origins if str(origin).strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _generic_audit_action(method: str, path: str) -> tuple[str, str, str | None] | None:
    """Map necessary business requests to low-volume generic audit events."""
    paper_detail = re.fullmatch(r"/api/papers/(\d+)", path)
    if method == "GET" and paper_detail:
        return "paper.view", "paper", paper_detail.group(1)
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        # Output routes and governance routes have richer explicit audit events.
        if any(part in path for part in ("export", "get-pdf", "download", "summary")):
            return None
        for prefix, resource, action_prefix in (
            ("/api/papers", "paper", "paper.change"),
            ("/api/drafts", "draft", "draft.change"),
            ("/api/videos", "video", "video.change"),
            ("/api/accounts", "channel_account", "channel_account.change"),
        ):
            if path.startswith(prefix):
                resource_id = next(iter(re.findall(r"/([0-9]+)(?:/|$)", path)), None)
                action = f"{resource}.delete" if method == "DELETE" else action_prefix
                return action, resource, resource_id
    return None


def _paper_output_action(method: str, path: str) -> tuple[str, str, str | None] | None:
    if method not in {"GET", "POST"}:
        return None
    rules = (
        ("/api/papers/export", "paper.export_excel"),
        ("/api/papers/batch-download-pdf", "paper.batch_pdf"),
        ("/api/papers/batch-generate-word-summary", "paper.generate_word_summary"),
    )
    for route, action in rules:
        if path == route:
            return action, "paper", None
    single = re.fullmatch(r"/api/papers/(\d+)/(get-pdf)", path)
    if single:
        return f"paper.{single.group(2).replace('-', '_')}", "paper", single.group(1)
    return None


@app.middleware("http")
async def request_context_and_audit(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    started_at = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f'app;dur={duration_ms:.1f}'
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if duration_ms >= 1000:
        logger.warning(
            "[SlowRequest] %s %s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )

    mapping = _generic_audit_action(request.method, request.url.path)
    if response.status_code >= 400 and not getattr(request.state, "governance_audited", False):
        mapping = mapping or _paper_output_action(request.method, request.url.path)
    authorization = request.headers.get("authorization", "")
    if mapping and authorization.lower().startswith("bearer "):
        try:
            from .dependencies import _decode_token
            from paper_library.governance import record_audit

            actor = _decode_token(authorization.split(" ", 1)[1])
            action, resource_type, resource_id = mapping
            audit_details = {
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            }
            audit_details.update(getattr(request.state, "audit_details", {}) or {})
            if resource_type == "paper":
                from .paper_audit import paper_identity_snapshot

                raw_paper_ids = audit_details.get("paper_ids")
                if isinstance(raw_paper_ids, list):
                    paper_ids = [value for value in raw_paper_ids if isinstance(value, int)]
                elif resource_id and resource_id.isdigit():
                    paper_ids = [int(resource_id)]
                else:
                    paper_ids = []
                snapshot = paper_identity_snapshot(paper_ids)
                audit_details.setdefault("paper_ids", snapshot["paper_ids"])
                if not audit_details.get("paper_internal_ids"):
                    audit_details["paper_internal_ids"] = snapshot["paper_internal_ids"]
            record_audit(
                actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_count=1 if resource_id else 0,
                result="success" if response.status_code < 400 else "failed",
                request_id=request_id,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details=audit_details,
            )
        except Exception:
            # Auditing must never make an otherwise valid business response fail.
            logger.debug('可选步骤失败，继续执行（request_context_and_audit）', exc_info=True)
            pass
    return response

for r in [auth.router, drafts.router, accounts.router, stats.router, papers.router, auto_login.router, authors.router, videos.router, dingtalk_auth.router, dingtalk_admin.router, literature_search.router, governance.router, config_options.router]:
    app.include_router(r)


# ──────────────────────────────────────────────────────────────
# Temporary file download endpoint (for native browser download)
# ──────────────────────────────────────────────────────────────

@app.get("/api/temp-download/{file_id}")
async def download_temp_file(
    file_id: str,
    expires: int,
    signature: str,
    request: Request,
    preview: bool = False,
):
    """Serve a signed temporary file as a download or a safe inline PDF preview."""
    import os
    from fastapi.responses import FileResponse
    from .temp_download import cleanup_expired, get_temp_file, verify_temp_download_url

    if not verify_temp_download_url(file_id, expires, signature):
        from paper_library.governance import record_artifact_access
        record_artifact_access(
            file_id,
            result="denied",
            request_id=getattr(request.state, "request_id", None),
        )
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="下载链接无效或已过期")

    # Periodic cleanup
    cleanup_expired()

    file_info = get_temp_file(file_id)
    if not file_info:
        from paper_library.governance import record_artifact_access
        record_artifact_access(
            file_id,
            result="failed",
            request_id=getattr(request.state, "request_id", None),
        )
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File not found or expired")

    # Verify file exists
    if not os.path.exists(file_info["path"]):
        from paper_library.governance import record_artifact_access
        record_artifact_access(
            file_id,
            result="failed",
            request_id=getattr(request.state, "request_id", None),
        )
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="File does not exist on server")

    from paper_library.governance import record_artifact_access
    record_artifact_access(
        file_id,
        result="success",
        request_id=getattr(request.state, "request_id", None),
    )

    inline_pdf = bool(
        preview
        and str(file_info["filename"]).lower().endswith(".pdf")
    )

    # Only PDFs may be rendered inline. Other generated artifacts always keep
    # attachment disposition so an HTML-like upload cannot execute in origin.
    return FileResponse(
        path=file_info["path"],
        filename=file_info["filename"],
        media_type="application/pdf" if inline_pdf else "application/octet-stream",
        content_disposition_type="inline" if inline_pdf else "attachment",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
