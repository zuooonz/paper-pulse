"""Isolated publisher process lifecycle and timeout handling."""

from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import signal
import time
import traceback

logger = logging.getLogger(__name__)
PUBLISH_CHANNEL_TIMEOUT_SECONDS = max(60, int(os.environ.get("PUBLISH_CHANNEL_TIMEOUT_SECONDS", "480")))


def publisher_process_entry(result_queue, account, draft_id, title, content, cover_images, image_doi_map) -> None:
    """Run a platform publisher in a child process."""
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        from publisher.registry import build_publisher_from_account

        publisher_obj = build_publisher_from_account(
            account,
            workspace=account.get("workspace", "."),
            config_dir=account.get("config_dir", "config"),
        )
        if publisher_obj is None:
            result_queue.put({
                "success": False,
                "channel": account.get("channel", "unknown"),
                "url": None,
                "error": "Publisher not implemented for this channel",
            })
            return
        result = publisher_obj.publish(
            draft_id=draft_id,
            title=title,
            content=content,
            cover_images=cover_images,
            image_doi_map=image_doi_map,
        )
        result_queue.put({
            "success": bool(result.success),
            "channel": result.channel,
            "url": result.url,
            "error": result.error,
        })
    except BaseException:
        result_queue.put({
            "success": False,
            "channel": account.get("channel", "unknown"),
            "url": None,
            "error": "发布失败，请稍后重试",
            "traceback": traceback.format_exc(),
        })


def terminate_publisher_process(process) -> None:
    """Terminate the isolated publisher and its browser process group."""
    if not process.is_alive():
        process.join(timeout=1)
        return
    terminated_group = False
    if os.name != "nt" and process.pid:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            terminated_group = True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if not terminated_group:
        process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        if os.name != "nt" and process.pid:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        else:
            process.kill()
        process.join(timeout=5)


def run_publisher_isolated(
    account: dict,
    draft_id: int,
    title: str,
    content: str,
    cover_images: list[str],
    image_doi_map: dict[str, str],
    is_cancelled,
    timeout_seconds: int | None = None,
) -> dict:
    """Publish in a child process with hard timeout and active cancellation."""
    timeout = timeout_seconds or PUBLISH_CHANNEL_TIMEOUT_SECONDS
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=publisher_process_entry,
        args=(result_queue, account, draft_id, title, content, cover_images, image_doi_map),
        daemon=True,
        name=f"publisher-{account.get('channel', 'unknown')}-{draft_id}",
    )
    started_at = time.monotonic()
    try:
        process.start()
        while process.is_alive():
            process.join(timeout=1)
            if is_cancelled():
                terminate_publisher_process(process)
                return {"success": False, "channel": account.get("channel", "unknown"), "url": None, "error": "已取消"}
            if time.monotonic() - started_at >= timeout:
                terminate_publisher_process(process)
                return {
                    "success": False,
                    "channel": account.get("channel", "unknown"),
                    "url": None,
                    "error": f"发布超时（超过 {timeout} 秒），浏览器进程已终止",
                }
        process.join(timeout=1)
        try:
            result = result_queue.get(timeout=2)
        except queue.Empty:
            result = {
                "success": False,
                "channel": account.get("channel", "unknown"),
                "url": None,
                "error": f"发布进程异常退出（exit code: {process.exitcode}）",
            }
        if result.get("traceback"):
            logger.error("[PublisherProcess] %s", result["traceback"])
        return result
    except BaseException:
        logger.exception("Could not start isolated publisher process")
        terminate_publisher_process(process)
        return {
            "success": False,
            "channel": account.get("channel", "unknown"),
            "url": None,
            "error": "无法启动发布进程，请查看服务端日志",
        }
    finally:
        result_queue.close()
        result_queue.join_thread()
