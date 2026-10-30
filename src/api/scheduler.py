"""Background scheduler for executing scheduled publishes."""
import datetime
import logging
import threading

from paper_library.db import get_session
from paper_library.models import PublishRecord

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_scheduler_stop_event = threading.Event()

# 卡死状态超时阈值（分钟）
STALE_STATE_TIMEOUT_MINUTES = 10


def reset_stale_paper_states(workspace: str, config_dir: str) -> None:
    """启动时清理卡在中间状态的论文记录。

    如果论文处于 generating / processing 状态超过阈值，
    自动重置为安全的前置状态，避免前端永久显示"进行中"。
    """
    from paper_library.models import Paper
    from paper_library.paper_lifecycle import reset_processing_to_pending
    from sqlalchemy import func

    # SQLite strips tzinfo from stored timestamps; SQLAlchemy normalizes bound values.
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        minutes=STALE_STATE_TIMEOUT_MINUTES
    )
    try:
        with get_session(workspace=workspace, config_dir=config_dir) as session:
            # 1) 生成推文卡死 -> 回滚到 not_generated
            stale_generating = (
                session.query(Paper)
                .filter(Paper.article_status == "generating")
                .filter(func.coalesce(Paper.updated_at, Paper.created_at) < cutoff)
                .all()
            )
            for p in stale_generating:
                p.article_status = "not_generated"
                logger.warning(
                    "[StaleState] Paper #%s reset article_status: generating -> not_generated",
                    p.id,
                )

            # 2) 论文处理卡死 -> 回滚到 pending
            stale_processing = (
                session.query(Paper)
                .filter(Paper.processing_status == "processing")
                .filter(func.coalesce(Paper.updated_at, Paper.created_at) < cutoff)
                .all()
            )
            for p in stale_processing:
                reset_processing_to_pending(p)
                logger.warning(
                    "[StaleState] Paper #%s reset processing_status to pending",
                    p.id,
                )

            if stale_generating or stale_processing:
                session.commit()
                logger.info(
                    "[StaleState] Reset %d generating + %d processing papers",
                    len(stale_generating),
                    len(stale_processing),
                )
            else:
                logger.info("[StaleState] No stale paper states found")
    except Exception as e:
        logger.exception("[StaleState] Failed to reset stale states: %s", e)


def start_scheduler(workspace: str, config_dir: str) -> None:
    """Start the scheduler in-process only for explicit single-process dev mode."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(workspace, config_dir),
        daemon=True,
        name="publish-scheduler",
    )
    _scheduler_thread.start()
    logger.info("[Scheduler] Started (check interval: 60s)")


def stop_scheduler(timeout: float = 5.0) -> None:
    """Signal the scheduler to stop and wait for it."""
    global _scheduler_thread
    _scheduler_stop_event.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=timeout)
        _scheduler_thread = None
    logger.info("[Scheduler] Stopped")


def _scheduler_loop(workspace: str, config_dir: str) -> None:
    """Compatibility entry point delegated to the DB-leased scheduler."""
    from worker.scheduler import run_scheduler

    run_scheduler(_scheduler_stop_event)


def _run_cookie_validation(workspace: str, config_dir: str) -> None:
    """Background task: validate all cookie accounts."""
    from tools.cookie_validator import validate_all_accounts
    results = validate_all_accounts(workspace, config_dir)
    valid_count = sum(1 for r in results if r.status.value == "valid")
    invalid_count = sum(1 for r in results if r.status.value == "invalid")
    logger.info(
        "[Scheduler] Daily cookie validation: %d total, %d valid, %d invalid",
        len(results), valid_count, invalid_count
    )
    return {"total": len(results), "valid": valid_count, "invalid": invalid_count}


def _make_aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    """Ensure a datetime is timezone-aware (UTC). Handles naive datetimes."""
    if dt is None:
        return dt
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        # SQLite strips tzinfo from values that were normalized to UTC on write.
        return dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(datetime.UTC)


def _should_execute(record: PublishRecord, now: datetime.datetime) -> bool:
    """Check whether a scheduled publish has reached its execution time."""
    sched_dt = _make_aware(record.scheduled_at)
    return sched_dt is not None and sched_dt <= now
