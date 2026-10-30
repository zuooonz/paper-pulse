#!/usr/bin/env python3
"""混合 API 与 durable queue 压测脚本。"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]


def _database_setup(concurrency: int) -> tuple[int, str | None, list[int], bool]:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from paper_library.db import get_session
    from paper_library.models import ArticleDraft, Paper

    run_id = uuid.uuid4().hex[:10]
    draft_ids: list[int] = []
    with get_session(str(ROOT_DIR), os.environ.get("CONFIG_DIR", "config")) as session:
        paper = session.query(Paper).order_by(Paper.id.asc()).first()
        created_paper = False
        if paper is None:
            paper = Paper(
                title=f"E2E load fixture {run_id}",
                abstract="Synthetic quantitative MRI record for load testing.",
                abstract_zh="压测用虚构论文记录。",
                journal_name="Magnetic Resonance in Medicine",
                year=2026,
                is_in_domain=True,
                processing_status="done", review_outcome="approved",
                source="e2e_load_test",
            )
            session.add(paper)
            session.flush()
            created_paper = True
        original_abstract_zh = paper.abstract_zh
        paper_id = int(paper.id)
        for index in range(max(concurrency * 2, 40)):
            draft = ArticleDraft(
                draft_type="e2e_load",
                title=f"E2E load draft {run_id}-{index}",
                content=f"# E2E load draft {run_id}-{index}\n\nLoad-test fixture content.",
                status="draft",
                meta={"source": "e2e.load_test", "run_id": run_id},
            )
            session.add(draft)
            session.flush()
            draft_ids.append(int(draft.id))
        return paper_id, original_abstract_zh, draft_ids, created_paper


def _cleanup_database(
    draft_ids: list[int], paper_id: int, created_paper: bool, original_abstract_zh: str | None
) -> None:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from paper_library.db import get_session
    from paper_library.models import ArticleDraft, Paper

    with get_session(str(ROOT_DIR), os.environ.get("CONFIG_DIR", "config")) as session:
        if draft_ids:
            session.query(ArticleDraft).filter(ArticleDraft.id.in_(draft_ids)).delete(synchronize_session=False)
        paper = session.get(Paper, paper_id)
        if paper is not None:
            if created_paper:
                session.delete(paper)
            else:
                paper.abstract_zh = original_abstract_zh


def _percentile(samples: list[float], percent: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil((percent / 100) * len(ordered)) - 1))
    return round(ordered[index], 2)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = os.environ.get("E2E_API_URL", "http://127.0.0.1:8000").rstrip("/")
    password = os.environ.get("E2E_ADMIN_PASSWORD", "E2E-test-admin-2026!")
    report_path = Path(
        args.report
        or os.environ.get("E2E_LOAD_REPORT")
        or Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs")) / "load-test.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(base_url=base_url, timeout=20, limits=httpx.Limits(
        max_connections=args.concurrency + 8,
        max_keepalive_connections=args.concurrency + 8,
    )) as auth_client:
        login = await auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        login.raise_for_status()
        token = login.json()["access_token"]

    paper_id, original_abstract_zh, draft_ids, paper_was_created = _database_setup(args.concurrency)
    # Restore the seed paper after load and remove the temporary drafts.
    run_id = uuid.uuid4().hex[:10]
    success_file = report_path.parent / f"load-test-noop-{run_id}.txt"
    worker_log = report_path.parent / f"load-test-worker-{run_id}.log"
    worker_env = os.environ.copy()
    worker_env["PYTHONPATH"] = f"{ROOT_DIR / 'src'}{os.pathsep}{ROOT_DIR}{os.pathsep}{worker_env.get('PYTHONPATH', '')}"
    worker_env["E2E_NOOP_SUCCESS_FILE"] = str(success_file)
    worker_env["JOBS_INLINE"] = "false"
    worker_env["JOBS_LEASE_TIMEOUT"] = "60"
    worker_env["JOBS_HEARTBEAT_INTERVAL"] = "5"
    worker_env["JOBS_POLL_INTERVAL"] = "0.05"
    worker_stream = worker_log.open("w", encoding="utf-8")
    worker = subprocess.Popen(
        [sys.executable, str(ROOT_DIR / "e2e" / "noop_worker.py"), "--concurrency", "8", "--kinds", "e2e_noop"],
        cwd=ROOT_DIR,
        env=worker_env,
        stdout=worker_stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    headers = {"Authorization": f"Bearer {token}"}
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    operations: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    queue_job_ids: list[str] = []
    queue_lock = asyncio.Lock()
    queue_limit = max(120, args.concurrency * 5)
    http_client = httpx.AsyncClient(
        base_url=base_url,
        timeout=20,
        headers=headers,
        limits=httpx.Limits(
            max_connections=args.concurrency + 8,
            max_keepalive_connections=args.concurrency + 8,
        ),
    )
    stop_at = time.monotonic() + args.duration
    rng = random.Random()

    async def measure(name: str, action) -> None:
        started = time.perf_counter()
        try:
            result = await action()
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            operations[name] += 1
            if isinstance(result, int):
                statuses[str(result)] += 1
                if result >= 400:
                    errors.append({"operation": name, "error": f"HTTP {result}"})
            elif result is None:
                statuses["SKIP"] += 1
            else:
                statuses["202"] += 1
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            latencies.append(elapsed)
            operations[name] += 1
            statuses["ERR"] += 1
            errors.append({"operation": name, "error": type(exc).__name__})

    async def enqueue_noop() -> int | None:
        async with queue_lock:
            if len(queue_job_ids) >= queue_limit:
                return None
            job_id = await asyncio.to_thread(
                _enqueue_noop,
                {"success_file": str(success_file), "sleep_min": 0.1, "sleep_max": 0.2},
            )
            queue_job_ids.append(job_id)
        return 202

    async def one_worker(worker_index: int) -> None:
        del worker_index
        while time.monotonic() < stop_at:
            choice = rng.choices(
                ["papers", "json_filter", "stats", "geo", "paper_patch", "draft_patch", "queue"],
                weights=[25, 20, 12, 10, 13, 12, 8],
                k=1,
            )[0]
            if choice == "papers":
                await measure(choice, lambda: _request_status(http_client, "GET", "/api/papers?page=1&page_size=10"))
            elif choice == "json_filter":
                await measure(
                    choice,
                    lambda: _request_status(
                        http_client,
                        "GET",
                        "/api/papers?research_topic=qMRI&has_multimodal=false&tag=qMRI&page=1&page_size=10",
                    ),
                )
            elif choice == "stats":
                await measure(choice, lambda: _request_status(http_client, "GET", "/api/stats/drafts"))
            elif choice == "geo":
                await measure(choice, lambda: _request_status(http_client, "GET", "/api/authors/geo?scope=all"))
            elif choice == "paper_patch":
                await measure(
                    choice,
                    lambda: _request_status(
                        http_client,
                        "PATCH",
                        f"/api/papers/{paper_id}",
                        json={"abstract_zh": f"{original_abstract_zh or ''}\nE2E-load-{run_id}"},
                    ),
                )
            elif choice == "draft_patch":
                draft_id = rng.choice(draft_ids)
                await measure(
                    choice,
                    lambda: _request_status(
                        http_client,
                        "PATCH",
                        f"/api/drafts/{draft_id}",
                        json={"title": f"E2E load update {run_id}-{rng.randrange(100000)}", "content": f"# E2E load update {run_id}"},
                    ),
                )
            else:
                await measure(choice, enqueue_noop)

    report: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "api_url": base_url,
        "concurrency": args.concurrency,
        "duration_seconds": args.duration,
        "paper_id": paper_id,
        "queue_limit": queue_limit,
        "errors": errors,
    }

    try:
        if worker.poll() is not None:
            raise RuntimeError(f"E2E no-op worker exited early; see {worker_log}")
        workers = [asyncio.create_task(one_worker(index)) for index in range(args.concurrency)]
        await asyncio.gather(*workers)

        if queue_job_ids:
            deadline = time.monotonic() + int(os.environ.get("E2E_LOAD_QUEUE_DRAIN_TIMEOUT", "60"))
            while time.monotonic() < deadline:
                states = await asyncio.to_thread(_queue_states, queue_job_ids)
                if all(status in {"succeeded", "failed", "cancelled"} for status in states.values()):
                    break
                await asyncio.sleep(0.5)
            states = await asyncio.to_thread(_queue_states, queue_job_ids)
            unsuccessful = [job_id for job_id in queue_job_ids if states.get(job_id) != "succeeded"]
            if unsuccessful:
                errors.extend({"operation": "queue_completion", "error": f"{len(unsuccessful)} jobs not succeeded"} for _ in [0])
            report["queue_jobs"] = len(queue_job_ids)
            report["queue_succeeded"] = sum(status == "succeeded" for status in states.values())
            report["queue_pending_or_failed"] = len(unsuccessful)

        await http_client.patch(
            f"/api/papers/{paper_id}",
            json={"abstract_zh": original_abstract_zh},
        )
        report.update({
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            "total_operations": len(latencies),
            "status_codes": dict(statuses),
            "operations": dict(operations),
            "error_count": len(errors),
            "error_rate": round(len(errors) / len(latencies), 5) if latencies else 0.0,
            "latency_ms": {
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "p99": _percentile(latencies, 99),
                "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            },
            "errors": errors,
            "success_file": str(success_file),
            "worker_log": str(worker_log),
        })
        return report
    finally:
        await http_client.aclose()
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        worker_stream.close()
        if queue_job_ids:
            try:
                await asyncio.to_thread(_cancel_remaining_jobs, queue_job_ids)
            except Exception:
                pass
        _cleanup_database(draft_ids, paper_id, paper_was_created, original_abstract_zh)


async def _request_status(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> int:
    response = await client.request(method, path, **kwargs)
    return response.status_code


def _enqueue_noop(payload: dict[str, Any]) -> str:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from worker.queue import enqueue

    return enqueue("e2e_noop", payload, max_attempts=3)


def _queue_states(job_ids: list[str]) -> dict[str, str]:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from worker.queue import get_job

    result = {}
    for job_id in job_ids:
        job = get_job(job_id)
        result[job_id] = str(job.get("status")) if job else "missing"
    return result


def _cancel_remaining_jobs(job_ids: list[str]) -> None:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from worker.queue import cancel

    for job_id in job_ids:
        cancel(job_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=30, help="并发数，范围 20–50")
    parser.add_argument("--duration", type=int, default=180, help="压测时长（秒）")
    parser.add_argument("--report", help="JSON 报告路径；默认写入 E2E_LOG_DIR")
    args = parser.parse_args()
    if not 20 <= args.concurrency <= 50:
        parser.error("--concurrency 必须在 20 到 50 之间")
    if args.duration < 1:
        parser.error("--duration 必须大于 0")
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:
        report = {
            "ok": False,
            "failure": {"type": type(exc).__name__, "message": str(exc)},
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        report_file = Path(args.report or os.environ.get("E2E_LOAD_REPORT", "/tmp/paper-pulse-e2e-logs/load-test.json"))
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    report["ok"] = report.get("error_count", 0) == 0
    report_file = Path(args.report or os.environ.get("E2E_LOAD_REPORT", Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs")) / "load-test.json"))
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
