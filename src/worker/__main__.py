"""Run database-backed job workers with ``python -m worker``."""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import signal
import threading
import uuid

from .queue import claim_next, complete, fail, heartbeat
from .registry import PermanentJobError, get_handler

logger = logging.getLogger("worker")
DEFAULT_KINDS = tuple(
    item.strip()
    for item in os.environ.get(
        "WORKER_KINDS",
        "publish_draft,publish_video,literature_search,poll_download,summary_job,cookie_validation,reconcile_pdf_flags",
    ).split(",")
    if item.strip()
)


def _run_job(job: dict, worker_id: str, stop: threading.Event) -> None:
    job_id = job["id"]
    done = threading.Event()
    interval = max(1, int(os.environ.get("JOBS_HEARTBEAT_INTERVAL", "30")))

    def heartbeat_loop() -> None:
        while not done.wait(interval):
            if not heartbeat(job_id, worker_id):
                logger.warning("Lost lease for job %s", job_id)
                return

    beat_thread = threading.Thread(target=heartbeat_loop, daemon=True, name=f"heartbeat-{job_id[:8]}")
    beat_thread.start()
    try:
        result = get_handler(job["kind"])(job["payload"], job_id)
        complete(job_id, result, worker_id=worker_id)
        logger.info("Job %s (%s) completed", job_id, job["kind"])
    except PermanentJobError as exc:
        logger.info("Job %s ended with a permanent business failure: %s", job_id, exc)
        fail(job_id, exc, worker_id=worker_id, retry=False)
    except BaseException as exc:
        logger.exception("Job %s (%s) failed", job_id, job["kind"])
        fail(job_id, exc, worker_id=worker_id)
    finally:
        done.set()
        beat_thread.join(timeout=2)


def _worker_loop(worker_id: str, kinds: tuple[str, ...], stop: threading.Event) -> None:
    poll_interval = max(0.1, float(os.environ.get("JOBS_POLL_INTERVAL", "1")))
    while not stop.is_set():
        job = claim_next(worker_id, kinds)
        if job is None:
            stop.wait(poll_interval)
            continue
        _run_job(job, worker_id, stop)


def run_worker(*, kinds: tuple[str, ...] = DEFAULT_KINDS, concurrency: int = 1) -> None:
    stop = threading.Event()

    def request_stop(signum, _frame):
        logger.info("Received signal %s; stopping worker loops", signum)
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, request_stop)

    concurrency = max(1, concurrency)
    prefix = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    logger.info("Starting %d worker loop(s) for kinds=%s", concurrency, ",".join(kinds))
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job-worker") as pool:
        futures = [pool.submit(_worker_loop, f"{prefix}-{index}", kinds, stop) for index in range(concurrency)]
        try:
            while not stop.wait(1):
                if any(future.done() for future in futures):
                    for future in futures:
                        if future.done() and future.exception():
                            logger.error("Worker loop stopped: %s", future.exception())
                    stop.set()
        finally:
            stop.set()
            pool.shutdown(wait=True, cancel_futures=False)
    logger.info("Worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable database job workers")
    parser.add_argument("--kinds", default=",".join(DEFAULT_KINDS), help="Comma-separated job kinds")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent jobs in this process")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    kinds = tuple(item.strip() for item in args.kinds.split(",") if item.strip())
    from api.config import settings
    from paper_library.db import init_db

    init_db(workspace=settings.workspace, config_dir=settings.config_dir)
    run_worker(kinds=kinds, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
