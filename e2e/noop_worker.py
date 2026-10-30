#!/usr/bin/env python3
"""注册 E2E 专用幂等轻任务，再进入项目的持久化 worker 主循环。"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _append_once(output_path: Path, job_id: str, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows fallback
        fcntl = None

    with lock_path.open("a+", encoding="utf-8") as lock_stream:
        if fcntl is not None:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            existing: set[str] = set()
            if output_path.exists():
                existing = {
                    line.strip()
                    for line in output_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }
            if job_id not in existing:
                with output_path.open("a", encoding="utf-8") as result_stream:
                    result_stream.write(job_id + "\n")
                    result_stream.flush()
                    os.fsync(result_stream.fileno())
            result_path = payload.get("result_jsonl")
            if result_path:
                record = Path(result_path)
                record.parent.mkdir(parents=True, exist_ok=True)
                with record.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"job_id": job_id}, ensure_ascii=False) + "\n")
        finally:
            if fcntl is not None:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _handler(payload: dict, job_id: str) -> dict:
    low = max(0.1, float(payload.get("sleep_min", 0.1)))
    high = min(1.0, max(low, float(payload.get("sleep_max", 1.0))))
    time.sleep(random.uniform(low, high))
    output = payload.get("success_file") or os.environ.get("E2E_NOOP_SUCCESS_FILE")
    if output:
        _append_once(Path(output), job_id, payload)
    return {"job_id": job_id, "kind": "e2e_noop", "ok": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--kinds", default="e2e_noop")
    args = parser.parse_args()

    from worker.registry import register

    register("e2e_noop", _handler)
    from worker.__main__ import main as worker_main

    # Keep the normal worker CLI and claim/lease/complete lifecycle intact.
    sys.argv = [sys.argv[0], "--concurrency", str(args.concurrency), "--kinds", args.kinds]
    worker_main()


if __name__ == "__main__":
    main()
