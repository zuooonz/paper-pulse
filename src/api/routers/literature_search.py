"""由管理员触发的论文检索后台任务。"""

import os
import logging
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config import settings
from ..dependencies import require_uploader
from paper_library.time_utils import format_api_datetime, utc_now
from ..error_responses import log_internal_error
from core.entity_detector import get_target_entity_key, load_entities_config


router = APIRouter(prefix="/api/literature-search", tags=["literature-search"])
ROOT = Path(__file__).resolve().parents[3]
MAX_LOG_LINES = 400
logger = logging.getLogger(__name__)
LITERATURE_SEARCH_TIMEOUT_SECONDS = max(1, int(os.environ.get("LITERATURE_SEARCH_TIMEOUT_SECONDS", "21600")))


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate a timed-out script and wait for its process tree to exit."""
    if process.poll() is not None:
        process.wait(timeout=1)
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Could not stop literature search process tree", exc_info=True)
        if process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        process.wait(timeout=5)


def _run_streaming_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    on_line,
    timeout_seconds: float,
) -> int:
    """Stream process output while enforcing a wall-clock timeout."""
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for output_line in process.stdout:
                lines.put(output_line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True, name="literature-search-stdout")
    reader.start()
    started_at = time.monotonic()
    try:
        output_finished = False
        while not output_finished:
            remaining = timeout_seconds - (time.monotonic() - started_at)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                line = lines.get(timeout=min(0.5, remaining))
            except queue.Empty:
                continue
            if line is None:
                output_finished = True
            else:
                on_line(line)

        remaining = timeout_seconds - (time.monotonic() - started_at)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        raise
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
        process.stdout.close()
        reader.join(timeout=2)

class LiteratureSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["recent", "range", "years"] = "recent"
    days: int = Field(default=60, ge=1, le=1095)
    date_from: date | None = None
    date_to: date | None = None
    year_from: int | None = Field(default=None, ge=2000, le=2100)
    year_to: int | None = Field(default=None, ge=2000, le=2100)
    entity_key: str | None = None
    dry_run: bool = False
    translate_zh: bool = True

    @model_validator(mode="after")
    def validate_range(self):
        if self.mode == "years":
            if self.year_from is None or self.year_to is None:
                raise ValueError("年份模式必须填写起止年份")
            if self.year_from > self.year_to:
                raise ValueError("起始年份不能晚于结束年份")
        elif self.mode == "range":
            if self.date_from is None or self.date_to is None:
                raise ValueError("日期范围模式必须填写起止日期")
            if self.date_from > self.date_to:
                raise ValueError("起始日期不能晚于结束日期")
            try:
                latest_end = self.date_from.replace(year=self.date_from.year + 3)
            except ValueError:
                # Feb 29 has no matching date in the third following year.
                latest_end = self.date_from.replace(year=self.date_from.year + 3, day=28)
            if self.date_to > latest_end:
                raise ValueError("日期范围不能超过三年")
        return self


def _now() -> str:
    return format_api_datetime(utc_now()) or ""


def _snapshot(job: dict) -> dict:
    result = job.get("result") or {}
    payload = job.get("payload") or {}
    request = result.get("request") or payload.get("request") or {}
    status = job["status"]
    stage = result.get("stage") or {
        "queued": "等待启动", "running": "启动检索", "succeeded": "已完成",
        "failed": "执行失败", "cancelled": "已取消",
    }.get(status, status)
    return {
        "task_id": job["id"],
        "job_id": job["id"],
        "status": status,
        "stage": stage,
        "request": request,
        "created_at": result.get("created_at") or _format_time(job.get("created_at")),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "logs": list(result.get("logs") or []),
        "report": dict(result.get("report") or {}),
        "error": (
            "检索失败，请查看服务端日志"
            if status == "failed"
            else result.get("error") or job.get("error")
        ),
    }


def _format_time(value) -> str:
    if value is None:
        return ""
    return format_api_datetime(value) or ""


def _set_stage(task: dict, line: str) -> None:
    if "[1/3] PMC" in line:
        task["stage"] = "检索 PMC"
    elif "[2/3] Europe PMC" in line:
        task["stage"] = "检索 Europe PMC"
    elif "[3/3] Google Scholar" in line:
        task["stage"] = "检索 Google Scholar"
    elif line.startswith("Dedup:"):
        task["stage"] = "结果去重"
    elif "[1/7] CrossRef" in line or line.startswith("[existing "):
        task["stage"] = "补全并入库"
    elif line.strip() == "Report":
        task["stage"] = "生成报告"

    match = re.match(
        r"\s*(Raw results|After dedup|New papers|Existing \(DB\)|Would import|Imported|Excluded|Entity patched|Existing enriched|Existing errors|Chinese translated):\s*(\d+)",
        line,
    )
    if match:
        key_map = {
            "Raw results": "raw_results",
            "After dedup": "after_dedup",
            "New papers": "new_papers",
            "Existing (DB)": "existing_papers",
            "Would import": "would_import",
            "Imported": "imported",
            "Excluded": "excluded",
            "Entity patched": "entity_patched",
            "Existing enriched": "existing_enriched",
            "Existing errors": "existing_errors",
            "Chinese translated": "translated_zh",
        }
        task["report"][key_map[match.group(1)]] = int(match.group(2))


def run_literature_search(task_id: str, payload: dict) -> dict:
    from worker.queue import update_result

    request = LiteratureSearchRequest.model_validate(payload["request"])
    task = {
        "status": "running",
        "stage": "启动检索",
        "request": request.model_dump(mode="json"),
        "created_at": payload.get("created_at") or _now(),
        "started_at": _now(),
        "finished_at": None,
        "logs": [],
        "report": {},
        "error": None,
        "started_by": payload.get("started_by"),
    }
    update_result(task_id, task)

    workspace = Path(settings.workspace)
    if not workspace.is_absolute():
        workspace = (ROOT / workspace).resolve()
    config_dir = Path(settings.config_dir)
    if not config_dir.is_absolute():
        config_dir = (ROOT / config_dir).resolve()

    entity_key = request.entity_key or get_target_entity_key()
    if not entity_key:
        task.update(status="failed", stage="执行失败", error="未配置目标课题，请先选择可检索课题。", finished_at=_now())
        update_result(task_id, task)
        raise RuntimeError(task["error"])

    command = [
        sys.executable,
        str(ROOT / "scripts" / "literature_search.py"),
        "--entity", entity_key,
        "--workspace", str(workspace),
        "--config-dir", str(config_dir),
    ]
    if request.mode == "years":
        command.extend(["--years", f"{request.year_from}-{request.year_to}"])
    elif request.mode == "range":
        command.extend(["--from", request.date_from.isoformat(), "--to", request.date_to.isoformat()])
    else:
        command.extend(["--days", str(request.days)])
    if request.dry_run:
        command.append("--dry-run")
    if request.translate_zh:
        command.append("--translate-zh")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        def record_line(raw_line: str) -> None:
            line = raw_line.rstrip()
            if not line:
                return
            task["logs"].append(line)
            task["logs"] = task["logs"][-MAX_LOG_LINES:]
            _set_stage(task, line)
            update_result(task_id, task)

        return_code = _run_streaming_process(
            command,
            cwd=ROOT,
            env=env,
            on_line=record_line,
            timeout_seconds=LITERATURE_SEARCH_TIMEOUT_SECONDS,
        )
        task["finished_at"] = _now()
        if return_code == 0:
            task.update(status="succeeded", stage="已完成")
        else:
            task.update(status="failed", stage="执行失败", error=f"脚本退出码 {return_code}")
            update_result(task_id, task)
            raise RuntimeError(task["error"])
    except subprocess.TimeoutExpired as exc:
        logger.error("Literature search exceeded %s seconds", LITERATURE_SEARCH_TIMEOUT_SECONDS)
        task.update(
            status="failed",
            stage="执行失败",
            error="检索执行超时，请稍后重试",
            finished_at=_now(),
        )
        task["logs"].append("[FATAL] 检索执行超时")
        update_result(task_id, task)
        raise RuntimeError(task["error"]) from exc
    except Exception as exc:
        log_internal_error(logger, "Literature search", exc)
        if task.get("error") is None:
            task.update(
                status="failed",
                stage="执行失败",
                error="检索失败，请查看服务端日志",
                finished_at=_now(),
            )
            task["logs"].append("[FATAL] 检索失败，请查看服务端日志")
        update_result(task_id, task)
        raise
    update_result(task_id, task)
    return task


@router.post("/tasks")
def start_literature_search(
    body: LiteratureSearchRequest,
    user: dict = Depends(require_uploader),
):
    from worker.queue import enqueue_exclusive, get_job

    payload = {"request": body.model_dump(mode="json"), "started_by": user.get("username"), "created_at": _now()}
    task_id, created = enqueue_exclusive("literature_search", payload, max_attempts=2)
    if not created:
        raise HTTPException(status_code=409, detail={
            "message": "已有检索任务正在运行",
            "task_id": task_id,
        })
    job = get_job(task_id)
    if job is None:
        raise HTTPException(status_code=503, detail="检索服务暂不可用，请稍后重试")
    return _snapshot(job)


@router.get("/tasks/latest")
def get_latest_literature_search(user: dict = Depends(require_uploader)):
    del user
    from worker.queue import find_jobs
    jobs = find_jobs(kind="literature_search", limit=1)
    return _snapshot(jobs[0]) if jobs else None


@router.get("/tasks/{task_id}")
def get_literature_search(task_id: str, user: dict = Depends(require_uploader)):
    del user
    from worker.queue import get_job
    task = get_job(task_id)
    if not task or task["kind"] != "literature_search":
        raise HTTPException(status_code=404, detail="任务不存在")
    return _snapshot(task)


@router.get("/entities")
def list_entities(user: dict = Depends(require_uploader)):
    """返回课题目录中配置了检索的课题。"""
    del user
    cfg = load_entities_config()
    entities = cfg.get("entities", {})
    return [
        {"key": key, "label": value.get("label", key)}
        for key, value in entities.items()
        if isinstance(value, dict) and value.get("search")
    ]
