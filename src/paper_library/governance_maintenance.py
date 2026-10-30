"""Safe backup and audit archival operations for the application database."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import warnings
from pathlib import Path

from .db import get_engine, get_session
from .governance_models import AuditEvent
from .time_utils import local_now, format_api_datetime
from tools.storage import get_storage_root


def backup_governance_database(destination_dir: str | Path | None = None) -> Path | None:
    """Create a consistent SQLite snapshot of the complete application DB."""
    engine = get_engine()
    if engine.dialect.name != "sqlite":
        warnings.warn("PostgreSQL 数据库备份请使用 pg_dump；已跳过本次文件备份。", RuntimeWarning)
        return None
    destination = Path(destination_dir) if destination_dir is not None else get_storage_root() / "backups"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"app_{local_now().strftime('%Y%m%d_%H%M%S')}.db"

    raw = engine.raw_connection()
    target = None
    try:
        import sqlite3

        target = sqlite3.connect(output)
        raw.driver_connection.backup(target)
        target.commit()
    finally:
        if target is not None:
            target.close()
        raw.close()
    return output


def archive_audit_events(
    before_date: dt.date,
    *,
    archive_dir: str | Path | None = None,
    delete_archived_views: bool = False,
) -> dict:
    """Write immutable gzip JSONL archive; optional deletion is view-only.

    Permission changes, approvals, exports, edits, and publishing events remain
    online even when ``delete_archived_views`` is requested.
    """
    destination = Path(archive_dir) if archive_dir is not None else get_storage_root() / "audit_archive"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"audit_before_{before_date.isoformat()}_{local_now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz"

    with get_session() as session:
        rows = session.query(AuditEvent).filter(AuditEvent.business_date < before_date).order_by(AuditEvent.id).all()
        with gzip.open(output, "wt", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps({
                    "event_id": row.event_id,
                    "occurred_at": format_api_datetime(row.occurred_at_utc),
                    "business_date": row.business_date.isoformat(),
                    "actor_id": row.actor_id,
                    "username": row.username,
                    "role": row.role,
                    "display_name": row.display_name,
                    "department": row.department,
                    "login_source": row.login_source,
                    "action": row.action,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "resource_count": row.resource_count,
                    "result": row.result,
                    "request_id": row.request_id,
                    "ip_address": row.ip_address,
                    "user_agent": row.user_agent,
                    "details": json.loads(row.details_json or "{}"),
                }, ensure_ascii=False, separators=(",", ":")) + "\n")

        deleted = 0
        if delete_archived_views:
            deleted = session.query(AuditEvent).filter(
                AuditEvent.business_date < before_date,
                AuditEvent.action.in_(("paper.view", "paper.artifact_download")),
            ).delete(synchronize_session=False)

    return {"path": str(output.resolve()), "archived": len(rows), "deleted_online": deleted}
