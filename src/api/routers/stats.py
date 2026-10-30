"""Statistics API router."""
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from paper_library.db import get_session
from paper_library.models import ArticleDraft, PublishRecord, VideoPublishRecord
from paper_library.time_utils import ensure_utc, format_api_datetime, to_local

from ..config import settings
from ..dependencies import require_content_access


TREND_DAYS = 30


def _recent_daily_trend(daily: Counter, *, days: int = TREND_DAYS, today: dt.date | None = None) -> list[dict]:
    """Return one entry per local day for the last ``days`` days, oldest first, zeros included."""
    end = today or to_local(dt.datetime.now(dt.UTC)).date()
    return [
        {"date": day.isoformat(), "count": daily.get(day.isoformat(), 0)}
        for day in (end - dt.timedelta(days=offset) for offset in range(days - 1, -1, -1))
    ]


router = APIRouter(prefix="/api/stats", tags=["stats"])


# ──────────────────────────────────────────────────────────────
# GET /api/stats/publish
# ──────────────────────────────────────────────────────────────

@router.get("/publish")
def publish_stats(user: dict = Depends(require_content_access)):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        all_records = session.query(PublishRecord).all()

        total = len(all_records)
        status_counts = Counter(r.status for r in all_records)
        success = status_counts.get("success", 0)
        failed = status_counts.get("failed", 0)
        scheduled = status_counts.get("scheduled", 0)

        # By channel+account
        channel_counter: dict[tuple, int] = Counter()
        for r in all_records:
            channel_counter[(r.channel or "", r.account_name or "")] += 1
        by_channel = [
            {"channel": ch, "account_name": an, "count": cnt}
            for (ch, an), cnt in sorted(channel_counter.items(), key=lambda x: -x[1])
        ]

        # Daily trend (by published_at date, success only)
        daily: Counter = Counter()
        for r in all_records:
            if r.status == "success" and r.published_at:
                day = to_local(r.published_at).date().isoformat()
                daily[day] += 1
        daily_trend = _recent_daily_trend(daily)

        # Recent 50 records (any status)
        recent = (
            session.query(PublishRecord, ArticleDraft.title)
            .outerjoin(ArticleDraft, ArticleDraft.id == PublishRecord.draft_id)
            .order_by(PublishRecord.created_at.desc())
            .limit(50)
            .all()
        )
        recent_records = [
            {
                "id": r.id,
                "draft_id": r.draft_id,
                "draft_title": draft_title if draft_title is not None else "（草稿已删除）",
                "channel": r.channel or "",
                "account_name": r.account_name or "",
                "status": r.status or "unknown",
                "url": r.url,
                "error": r.error,
                "scheduled_at": format_api_datetime(r.scheduled_at),
                "published_at": format_api_datetime(r.published_at),
                "created_at": format_api_datetime(r.created_at),
            }
            for r, draft_title in recent
        ]

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "scheduled": scheduled,
            "by_channel": by_channel,
            "daily_trend": daily_trend,
            "recent_records": recent_records,
        }


# ──────────────────────────────────────────────────────────────
# GET /api/stats/drafts
# ──────────────────────────────────────────────────────────────

@router.get("/drafts")
def draft_stats(user: dict = Depends(require_content_access)):
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        rows = (
            session.query(ArticleDraft.status, func.count(ArticleDraft.id))
            .group_by(ArticleDraft.status)
            .all()
        )
        distribution = {status: count for status, count in rows}
        total = sum(distribution.values())
        return {
            "total": total,
            "distribution": distribution,
            "draft": distribution.get("draft", 0),
            "approved": distribution.get("approved", 0),
            "rejected": distribution.get("rejected", 0),
            "published": distribution.get("published", 0),
        }


# ──────────────────────────────────────────────────────────────
# GET /api/stats/video-publish  —  video publish stats (full)
# ──────────────────────────────────────────────────────────────

@router.get("/video-publish")
def video_publish_stats(user: dict = Depends(require_content_access)):
    """Return full publish stats for videos (video_publish_records table)."""
    # Use the shared application database session.
    with get_session(str(Path.cwd()), "config") as session:
        records = session.query(VideoPublishRecord).order_by(
            VideoPublishRecord.created_at.desc()
        ).all()

        all_records = [{
            "id": record.id, "video_id": record.video_id, "account_id": record.account_id,
            "channel": record.channel or "", "account_name": record.account_name or "",
            "status": record.status or "unknown", "publish_url": record.publish_url,
            "error": record.error, "scheduled_at": record.scheduled_at,
            "published_at": record.published_at, "created_at": record.created_at,
        } for record in records]

        total = len(all_records)
        status_counts = Counter(r["status"] for r in all_records)
        success = status_counts.get("success", 0)
        failed = status_counts.get("failed", 0)
        scheduled = status_counts.get("scheduled", 0)

        # By channel+account
        channel_counter: dict[tuple, int] = Counter()
        for r in all_records:
            channel_counter[(r["channel"], r["account_name"])] += 1
        by_channel = [
            {"channel": ch, "account_name": an, "count": cnt}
            for (ch, an), cnt in sorted(channel_counter.items(), key=lambda x: -x[1])
        ]

        # Daily trend (success only)
        daily: Counter = Counter()
        for r in all_records:
            if r["status"] == "success" and r["published_at"]:
                day = to_local(ensure_utc(r["published_at"])).date().isoformat()
                daily[day] += 1
        daily_trend = _recent_daily_trend(daily)

        # Recent 50 records
        recent_records = []
        for r in all_records[:50]:
            recent_records.append({
                "id": r["id"], "video_id": r["video_id"],
                "channel": r["channel"], "account_name": r["account_name"],
                "status": r["status"], "url": r["publish_url"],
                "error": r["error"],
                "scheduled_at": format_api_datetime(r["scheduled_at"]),
                "published_at": format_api_datetime(r["published_at"]),
                "created_at": format_api_datetime(r["created_at"]),
            })

        return {
            "total": total,
            "success": success,
            "failed": failed,
            "scheduled": scheduled,
            "by_channel": by_channel,
            "daily_trend": daily_trend,
            "recent_records": recent_records,
        }
