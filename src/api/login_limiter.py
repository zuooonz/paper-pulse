"""Small in-process login throttling guard.

The proxy should still rate-limit at the edge. This guard protects a single API
worker from password-guessing and PBKDF2 CPU exhaustion when deployed directly.
"""
from __future__ import annotations

import math
import threading
import time


class LoginRateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__("too many login attempts")
        self.retry_after = max(1, retry_after)


class LoginRateLimiter:
    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 15 * 60,
        base_lock_seconds: int = 30,
        max_lock_seconds: int = 15 * 60,
        clock=time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.base_lock_seconds = base_lock_seconds
        self.max_lock_seconds = max_lock_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (failure_count, last_failure_at, locked_until)
        self._state: dict[str, tuple[int, float, float]] = {}

    def _prune(self, key: str, now: float) -> tuple[int, float, float] | None:
        state = self._state.get(key)
        if state is None:
            return None
        count, last, locked_until = state
        if locked_until <= now and now - last > self.window_seconds:
            self._state.pop(key, None)
            return None
        return count, last, locked_until

    def check(self, keys: tuple[str, ...]) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                state = self._prune(key, now)
                if state and state[2] > now:
                    raise LoginRateLimited(math.ceil(state[2] - now))

    def record_failure(self, keys: tuple[str, ...]) -> None:
        now = self._clock()
        with self._lock:
            for key in keys:
                count, _last, _locked = self._prune(key, now) or (0, now, 0.0)
                count += 1
                locked_until = 0.0
                if count >= self.max_failures:
                    lock_seconds = min(
                        self.base_lock_seconds * (2 ** (count - self.max_failures)),
                        self.max_lock_seconds,
                    )
                    locked_until = now + lock_seconds
                self._state[key] = (count, now, locked_until)

    def reset(self, keys: tuple[str, ...]) -> None:
        with self._lock:
            for key in keys:
                self._state.pop(key, None)


login_limiter = LoginRateLimiter()
