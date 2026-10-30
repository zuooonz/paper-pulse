"""Helpers for logging internal failures without exposing them in responses."""

from __future__ import annotations

import logging

from fastapi import HTTPException


def log_internal_error(logger: logging.Logger, operation: str, exc: BaseException) -> None:
    """Write the exception and traceback to server logs for operator diagnosis."""
    logger.error(
        "%s failed (%s)",
        operation,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def internal_http_error(
    logger: logging.Logger,
    operation: str,
    exc: BaseException,
    *,
    status_code: int = 500,
    detail: str = "服务器内部错误，请稍后重试",
) -> HTTPException:
    """Log a failure and return a stable, non-sensitive HTTP error."""
    log_internal_error(logger, operation, exc)
    return HTTPException(status_code=status_code, detail=detail)
