#!/usr/bin/env python3
"""最小 OpenAI Chat Completions 兼容服务，供 E2E 使用。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


_REQUEST_COUNT = 0
_COUNT_LOCK = threading.Lock()

MARKDOWN_DRAFT = """# qMRI 虚构研究：定量参数图的稳定性验证

这是一篇仅用于端到端测试的虚构论文草稿。研究关注定量磁共振成像中参数图的重复性，以及扫描条件对测量结果的影响。

## 研究方法

虚构流程以 qMRLab 软件和 hMRI toolbox for SPM 作为分析工具示例，对模拟数据中的 T1、T2 参数图进行比较。所有结果均为虚构示例，不代表真实临床结论。

## 主要发现

在本示例的模拟条件下，重复测量结果保持稳定。后续研究仍需在独立样本中验证这一观察。

## 结论

本草稿用于检查生成、审核和工作台展示流程，不应作为医学建议或真实研究报告。
"""

JSON_DRAFT = {
    "report_title": "qMRI 虚构研究总结",
    "report_subtitle": "端到端测试数据",
    "overview": "这是一份仅供测试的虚构 qMRI 研究总结，用于验证结构化 JSON 响应。",
    "narrative": {
        "technical_scope": {"content": "示例研究讨论定量磁共振成像的参数图。"},
        "population_topics": {
            "content": "研究对象为虚构的模拟数据。",
            "subsections": [{"heading": "研究主题", "content": "检查参数图重复性。"}],
        },
        "experimental_design": {"content": "研究使用两组模拟数据进行重复测量。"},
        "method_analysis": {
            "heading": "参数图分析",
            "content": "使用 qMRLab software 对模拟数据执行分析；hMRI toolbox 仅作为实体识别示例名称。",
        },
        "core_findings": {
            "content": "模拟结果仅用于验证测试流程。",
            "subsections": [{"heading": "重复性", "content": "示例测量结果保持稳定。"}],
        },
        "conclusion": {
            "content": "这不是一项真实研究。",
            "bullets": ["虚构样例", "不含真实患者数据", "仅用于软件测试"],
        },
    },
}


def _requests_json_mode(payload: dict[str, Any]) -> bool:
    if payload.get("response_format"):
        return True
    messages = payload.get("messages") or []
    return "json" in json.dumps(messages, ensure_ascii=False).casefold()


class Handler(BaseHTTPRequestHandler):
    server_version = "paper-pulse-e2e-mock-llm/1.0"

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "e2e-mock-model",
                            "object": "model",
                            "created": 0,
                            "owned_by": "e2e",
                        }
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global _REQUEST_COUNT
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "Not found", "type": "not_found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": {"message": "Invalid JSON", "type": "invalid_request_error"}})
            return

        with _COUNT_LOCK:
            _REQUEST_COUNT += 1
            count = _REQUEST_COUNT
        print(
            f"{dt.datetime.now(dt.UTC).isoformat()} chat.completions request={count} "
            f"model={payload.get('model', 'e2e-mock-model')}",
            flush=True,
        )

        content = (
            json.dumps(JSON_DRAFT, ensure_ascii=False)
            if _requests_json_mode(payload)
            else MARKDOWN_DRAFT
        )
        self._send_json(
            200,
            {
                "id": f"chatcmpl-e2e-{count}",
                "object": "chat.completion",
                "created": int(dt.datetime.now(dt.UTC).timestamp()),
                "model": payload.get("model") or "e2e-mock-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 64,
                    "completion_tokens": max(1, len(content) // 3),
                    "total_tokens": 64 + max(1, len(content) // 3),
                },
            },
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep standard server logs concise while preserving the request count above.
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Mock LLM listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
