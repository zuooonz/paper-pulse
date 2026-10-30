"""Bounded streaming helpers for multipart uploads."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)
UPLOAD_CHUNK_BYTES = 1024 * 1024
BYTES_PER_MB = 1024 * 1024


def _read_upload_limit_mb(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning("Invalid %s; using default %s MB", name, default)
        return default
    return value


PDF_MAX_UPLOAD_MB = _read_upload_limit_mb("PDF_MAX_UPLOAD_MB", 100)
WORD_MAX_UPLOAD_MB = _read_upload_limit_mb("WORD_MAX_UPLOAD_MB", 50)
VIDEO_MAX_UPLOAD_MB = _read_upload_limit_mb("VIDEO_MAX_UPLOAD_MB", 2048)
COVER_MAX_UPLOAD_MB = _read_upload_limit_mb("COVER_MAX_UPLOAD_MB", 20)

PDF_MAX_UPLOAD_BYTES = PDF_MAX_UPLOAD_MB * BYTES_PER_MB
WORD_MAX_UPLOAD_BYTES = WORD_MAX_UPLOAD_MB * BYTES_PER_MB
VIDEO_MAX_UPLOAD_BYTES = VIDEO_MAX_UPLOAD_MB * BYTES_PER_MB
COVER_MAX_UPLOAD_BYTES = COVER_MAX_UPLOAD_MB * BYTES_PER_MB


def format_upload_limit(max_bytes: int) -> str:
    """Format a configured byte limit for upload error messages."""
    megabytes = max_bytes / BYTES_PER_MB
    if megabytes >= 1024 and megabytes % 1024 == 0:
        return f"{megabytes / 1024:g} GB"
    return f"{megabytes:g} MB"


class UploadTooLarge(ValueError):
    """Raised when a stream exceeds its configured per-file limit."""


def upload_size(upload) -> int | None:
    """Return the known upload size without consuming its content."""
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size >= 0:
        return size

    stream = getattr(upload, "file", upload)
    try:
        current = stream.tell()
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(current)
    except (AttributeError, OSError, ValueError):
        return None
    return size if isinstance(size, int) and size >= 0 else None


def validate_upload_size(upload, max_bytes: int, *, label: str = "文件") -> None:
    """Reject known oversized uploads before creating application files."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    size = upload_size(upload)
    if size is not None and size > max_bytes:
        raise UploadTooLarge(f"{label}超过大小限制")


def copy_upload(
    upload,
    destination: BinaryIO,
    max_bytes: int,
    *,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> int:
    """Copy an upload in bounded chunks and stop at the first excess byte."""
    if max_bytes < 0 or chunk_bytes < 1:
        raise ValueError("upload limits must be positive")
    source = getattr(upload, "file", upload)
    written = 0
    while True:
        chunk = source.read(min(chunk_bytes, max_bytes - written + 1))
        if not chunk:
            return written
        if written + len(chunk) > max_bytes:
            raise UploadTooLarge("文件超过大小限制")
        destination.write(chunk)
        written += len(chunk)


def copy_upload_to_path(upload, path: Path, max_bytes: int) -> int:
    """Create a new file from an upload and remove partial output on failure."""
    try:
        with path.open("wb") as output:
            return copy_upload(upload, output, max_bytes)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
