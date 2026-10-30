"""Temporary file storage for native browser downloads.

Two-step download approach:
1. POST to create file → returns temp_url
2. GET temp_url → browser handles download with native progress

Files are stored under the configured temporary root and auto-cleaned after 7 days.
"""
import logging
import shutil

import time
import uuid
import hashlib
import hmac
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from .config import settings
from tools.storage import get_temp_dir as get_configured_temp_dir

logger = logging.getLogger(__name__)
def get_temp_dir() -> Path:
    """Get the configured application temp directory."""
    temp_dir = get_configured_temp_dir(settings.workspace)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

# Shared download links and their files remain available for 7 days.
EXPIRE_SECONDS = 7 * 24 * 60 * 60


def _download_signature(file_id: str, expires_at: int) -> str:
    """Create a short-lived signature without exposing the user's login token."""
    payload = f"{file_id}:{expires_at}".encode("utf-8")
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def build_temp_download_url(file_info: dict) -> str:
    """Build a relative, signed URL consumable by browsers and DingTalk clients."""
    file_id = str(file_info["file_id"])
    expires_at = int(file_info["expires_at"])
    query = urlencode({
        "expires": expires_at,
        "signature": _download_signature(file_id, expires_at),
    })
    return f"/api/temp-download/{file_id}?{query}"


def verify_temp_download_url(file_id: str, expires_at: int, signature: str) -> bool:
    """Validate expiry and signature for a temporary file URL."""
    if expires_at < int(time.time()):
        return False
    expected = _download_signature(file_id, expires_at)
    return hmac.compare_digest(expected, signature)


def _safe_filename(filename: str) -> str:
    name = Path(filename or "download").name
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name).strip()
    return (cleaned or "download")[:180]


def cleanup_expired() -> None:
    """Remove expired temporary files (also called before creating a new one)."""
    temp_dir = get_temp_dir()
    now = time.time()
    for file_path in temp_dir.iterdir():
        if not file_path.is_file():
            continue
        try:
            if now - file_path.stat().st_mtime > EXPIRE_SECONDS:
                file_path.unlink()
        except OSError:
            logger.debug("Could not remove expired temp file %s", file_path, exc_info=True)


def _create_temp_file(filename: str, content_type: str) -> tuple[str, int, Path]:
    file_id = str(uuid.uuid4())
    expires_at = int(time.time()) + EXPIRE_SECONDS
    temp_dir = get_temp_dir()
    cleanup_expired()
    file_path = temp_dir / f"{file_id}_{_safe_filename(filename)}"
    return file_id, expires_at, file_path


def create_temp_file(content: bytes, filename: str, content_type: str) -> dict:
    """Create a temporary file and return download info.

    Args:
        content: File content as bytes
        filename: Original filename (for Content-Disposition)
        content_type: MIME type

    Returns:
        dict with temp_url, filename, content_type, expires_at
    """
    file_id, expires_at, file_path = _create_temp_file(filename, content_type)
    file_path.write_bytes(content)
    return {
        "file_id": file_id,
        "filename": _safe_filename(filename),
        "content_type": content_type,
        "expires_at": expires_at,
        "path": str(file_path),
    }


def create_temp_file_from_path(source_path: Path, filename: str, content_type: str) -> dict:
    """Stream a file into temp storage without loading it into memory."""
    file_id, expires_at, file_path = _create_temp_file(filename, content_type)
    with source_path.open("rb") as source, file_path.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    return {
        "file_id": file_id,
        "filename": _safe_filename(filename),
        "content_type": content_type,
        "expires_at": expires_at,
        "path": str(file_path),
    }


def get_temp_file(file_id: str) -> Optional[dict]:
    """Get temp file info by ID.

    Args:
        file_id: Unique file ID

    Returns:
        dict with file info, or None if not found/expired
    """
    temp_dir = get_temp_dir()
    # Find file by ID
    for file_path in temp_dir.iterdir():
        if file_path.name.startswith(f"{file_id}_"):
            # Check expiration
            try:
                # Extract timestamp from filename or use file mtime
                mtime = file_path.stat().st_mtime
                if time.time() - mtime > EXPIRE_SECONDS:
                    # Expired, delete
                    file_path.unlink()
                    return None
                return {
                    "file_id": file_id,
                    "filename": file_path.name[len(file_id) + 1:],  # Remove ID prefix
                    "path": str(file_path),
                }
            except Exception:
                logger.debug('可选步骤失败，继续执行（get_temp_file）', exc_info=True)
                return None
    return None


