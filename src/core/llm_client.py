from __future__ import annotations

import time
from typing import Any, Dict, List

import requests


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 120,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    @staticmethod
    def _response_error(data: Any, body: str) -> str:
        """Return a useful, secret-free description for non-standard responses."""
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                code = error.get("code") or error.get("type")
                message = error.get("message") or error.get("msg")
                if code and message:
                    return f"{code}: {message}"
                if message:
                    return str(message)
            elif error:
                return str(error)

            code = data.get("code") or data.get("status")
            message = data.get("message") or data.get("msg") or data.get("detail")
            if code and message:
                return f"{code}: {message}"
            if message:
                return str(message)

            return f"响应字段: {', '.join(str(key) for key in data.keys())}"

        return body[:500] or "空响应"

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.4,
        max_tokens: int | None = None,
        timeout_override: int | None = None,
        thinking: bool | None = None,
        response_format: str | None = None,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("API key missing in runtime config")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        # Optional reasoning mode control supported by some compatible services.
        if thinking is not None:
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
        if thinking is False or thinking is None:
            payload["temperature"] = temperature
        if max_tokens is not None and max_tokens > 0:
            payload["max_tokens"] = int(max_tokens)
        if response_format:
            payload["response_format"] = {"type": response_format}

        # Use per-model timeout if provided, otherwise fall back to default
        read_timeout = timeout_override or self.timeout_seconds
        # Connection timeout is always shorter
        connect_timeout = min(30, read_timeout)
        timeout_tuple = (connect_timeout, read_timeout)

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout_tuple,
                )
                # Retry on server errors (5xx)
                if resp.status_code >= 500:
                    last_exc = requests.exceptions.HTTPError(
                        f"Server error {resp.status_code}: {resp.text[:200]}",
                        response=resp,
                    )
                    if attempt < self.max_retries:
                        wait = 5 * (3 ** (attempt - 1))  # 5s, 15s, 45s
                        print(
                            f"[LLM] 服务端错误 {resp.status_code}，{wait}s 后重试 ({attempt}/{self.max_retries})",
                            flush=True,
                        )
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()

                # Retry on rate limiting (429)
                if resp.status_code == 429:
                    last_exc = requests.exceptions.HTTPError(
                        f"Rate limited: {resp.text[:200]}",
                        response=resp,
                    )
                    if attempt < self.max_retries:
                        # Longer backoff for persistent rate limits
                        wait = 120 * (2 ** (attempt - 1))  # 120s, 240s, 480s
                        print(
                            f"[LLM] 请求限流 429，{wait}s 后重试 ({attempt}/{self.max_retries})",
                            flush=True,
                        )
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()

                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError:
                    last_exc = RuntimeError(
                        f"LLM 返回非 JSON 内容（HTTP {resp.status_code}）: {resp.text[:500]}"
                    )
                else:
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if choices and isinstance(choices, list):
                        choice = choices[0]
                        message = choice.get("message") if isinstance(choice, dict) else None
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str) and content:
                            usage = data.get("usage") if isinstance(data, dict) else None
                            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
                            finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                            print(
                                f"[LLM] 完成 model={model} finish_reason={finish_reason} "
                                f"content_chars={len(content)} completion_tokens={completion_tokens}",
                                flush=True,
                            )
                            return content
                        last_exc = RuntimeError("LLM 响应 choices 中缺少 message.content")
                    else:
                        detail = self._response_error(data, resp.text)
                        last_exc = RuntimeError(
                            f"LLM 返回非标准响应（HTTP {resp.status_code}，缺少 choices）: {detail}"
                        )

                if attempt < self.max_retries:
                    wait = 5 * (3 ** (attempt - 1))
                    print(
                        f"[LLM] 响应格式异常，{wait}s 后重试 ({attempt}/{self.max_retries}): {last_exc}",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue

            except (
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ConnectTimeout,
            ) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    wait = 5 * (3 ** (attempt - 1))  # 5s, 15s, 45s
                    print(
                        f"[LLM] 请求超时/连接失败，{wait}s 后重试 ({attempt}/{self.max_retries}): {type(exc).__name__}",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue

        # All retries exhausted
        raise RuntimeError(
            f"LLM 请求在 {self.max_retries} 次重试后仍失败: {last_exc}"
        ) from last_exc
