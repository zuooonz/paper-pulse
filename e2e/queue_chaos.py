#!/usr/bin/env python3
"""对 durable jobs 队列执行进程崩溃与租约回收演练。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


def _enqueue_jobs(count: int, success_file: Path) -> list[str]:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from worker.queue import enqueue

    result = []
    for index in range(count):
        job_id = enqueue(
            "e2e_noop",
            {
                "success_file": str(success_file),
                # A long in-range sleep makes it practical to kill a worker while
                # its currently leased job is still executing.
                "sleep_min": 0.85,
                "sleep_max": 1.0,
                "index": index,
            },
            max_attempts=5,
        )
        result.append(job_id)
    return result


def _read_jobs(job_ids: list[str]) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from worker.queue import get_job

    return {job_id: get_job(job_id) or {} for job_id in job_ids}


def _terminate_workers(workers: list[subprocess.Popen], killed_pid: int | None) -> None:
    for process in workers:
        if process.pid == killed_pid or process.poll() is not None:
            continue
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:  # pragma: no cover - E2E workers are normally run on POSIX.
                process.terminate()
        except ProcessLookupError:
            pass
        except OSError:
            process.terminate()
    for process in workers:
        if process.pid == killed_pid or process.poll() is not None:
            continue
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:  # pragma: no cover
                    process.kill()
            except OSError:
                process.kill()
            process.wait(timeout=5)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must point to the E2E database")

    log_dir = Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    success_file = log_dir / f"queue-chaos-success-{run_id}.txt"
    report_path = Path(
        args.report
        or os.environ.get("E2E_QUEUE_CHAOS_REPORT")
        or log_dir / "queue-chaos.json"
    )
    worker_logs = [log_dir / f"queue-chaos-worker-{run_id}-{index}.log" for index in range(3)]
    worker_env = os.environ.copy()
    worker_env["PYTHONPATH"] = f"{ROOT_DIR / 'src'}{os.pathsep}{ROOT_DIR}{os.pathsep}{worker_env.get('PYTHONPATH', '')}"
    worker_env["E2E_NOOP_SUCCESS_FILE"] = str(success_file)
    worker_env["JOBS_INLINE"] = "false"
    worker_env["JOBS_LEASE_TIMEOUT"] = str(args.lease_timeout)
    worker_env["JOBS_HEARTBEAT_INTERVAL"] = "2"
    worker_env["JOBS_POLL_INTERVAL"] = "0.05"

    workers: list[subprocess.Popen] = []
    streams = []
    killed_pid: int | None = None
    job_ids: list[str] = []
    report: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "jobs_requested": args.jobs,
        "lease_timeout_seconds": args.lease_timeout,
        "result_file": str(success_file),
    }
    try:
        for index, log_path in enumerate(worker_logs):
            stream = log_path.open("w", encoding="utf-8")
            streams.append(stream)
            process = subprocess.Popen(
                [sys.executable, str(ROOT_DIR / "e2e" / "noop_worker.py"), "--concurrency", "1", "--kinds", "e2e_noop"],
                cwd=ROOT_DIR,
                env=worker_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            workers.append(process)
            time.sleep(0.15)
            if process.poll() is not None:
                raise RuntimeError(f"E2E worker {index} exited early; see {log_path}")

        job_ids = _enqueue_jobs(args.jobs, success_file)
        report["job_ids"] = job_ids

        deadline = time.monotonic() + min(30, args.timeout)
        while time.monotonic() < deadline:
            live_jobs = _read_jobs(job_ids)
            running = [
                job for job in live_jobs.values()
                if job.get("status") == "running" and job.get("locked_by")
            ]
            if running:
                owner_pid_text = str(running[0]["locked_by"]).split("-", 1)[0]
                if owner_pid_text.isdigit() and int(owner_pid_text) in {item.pid for item in workers}:
                    killed_pid = int(owner_pid_text)
                    os.kill(killed_pid, signal.SIGKILL)
                    workers[[item.pid for item in workers].index(killed_pid)].wait(timeout=5)
                    report["killed_worker_pid"] = killed_pid
                    report["killed_job_id"] = running[0].get("id")
                    break
            if all(job.get("status") in {"succeeded", "failed", "cancelled"} for job in live_jobs.values()):
                break
            time.sleep(0.01)
        else:
            raise RuntimeError("No worker was observed holding a running lease before the deadline")

        finish_deadline = time.monotonic() + args.timeout
        while time.monotonic() < finish_deadline:
            states = _read_jobs(job_ids)
            if all((states.get(job_id) or {}).get("status") == "succeeded" for job_id in job_ids):
                break
            if any((states.get(job_id) or {}).get("status") == "failed" for job_id in job_ids):
                break
            time.sleep(0.25)

        final_jobs = _read_jobs(job_ids)
        state_counts: dict[str, int] = {}
        for job in final_jobs.values():
            status = str(job.get("status", "missing"))
            state_counts[status] = state_counts.get(status, 0) + 1
        successful_ids = {job_id for job_id, job in final_jobs.items() if job.get("status") == "succeeded"}
        lines = success_file.read_text(encoding="utf-8").splitlines() if success_file.exists() else []
        result_ids = [line.strip() for line in lines if line.strip()]
        duplicates = sorted({job_id for job_id in result_ids if result_ids.count(job_id) > 1})
        missing = sorted(set(job_ids) - set(result_ids))
        unexpected = sorted(set(result_ids) - set(job_ids))
        report.update({
            "finished_at": dt.datetime.now(dt.UTC).isoformat(),
            "job_states": state_counts,
            "attempts": {job_id: int(job.get("attempts") or 0) for job_id, job in final_jobs.items()},
            "result_count": len(result_ids),
            "duplicate_success_ids": duplicates,
            "missing_success_ids": missing,
            "unexpected_success_ids": unexpected,
            "succeeded_job_count": len(successful_ids),
            "success": (
                len(successful_ids) == args.jobs
                and len(result_ids) == args.jobs
                and not duplicates
                and not missing
                and not unexpected
                and successful_ids == set(job_ids)
            ),
            "worker_logs": [str(item) for item in worker_logs],
        })
        if not report["success"]:
            raise AssertionError("Queue chaos run did not recover every job exactly once")
        return report
    except Exception as exc:
        report["success"] = False
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        if job_ids:
            final_jobs = _read_jobs(job_ids)
            report["job_states"] = {
                status: sum(job.get("status") == status for job in final_jobs.values())
                for status in {str(job.get("status", "missing")) for job in final_jobs.values()}
            }
            report["job_ids"] = job_ids
        raise
    finally:
        _terminate_workers(workers, killed_pid)
        for stream in streams:
            stream.close()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report["finished_at"] = report.get("finished_at") or dt.datetime.now(dt.UTC).isoformat()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=60, help="入队任务数")
    parser.add_argument("--lease-timeout", type=int, default=5, help="租约过期秒数")
    parser.add_argument("--timeout", type=int, default=180, help="等待租约回收与任务完成的上限秒数")
    parser.add_argument("--report", help="JSON 报告路径；默认写入 E2E_LOG_DIR")
    args = parser.parse_args()
    if args.jobs < 3:
        parser.error("--jobs 至少为 3，确保三个 worker 都有机会领取任务")
    if args.lease_timeout < 2 or args.timeout <= args.lease_timeout:
        parser.error("--lease-timeout 至少为 2，且 --timeout 必须更大")
    try:
        report = run(args)
    except Exception as exc:
        print(json.dumps({"success": False, "failure": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
