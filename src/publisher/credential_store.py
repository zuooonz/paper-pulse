"""加密和解密渠道账号凭据。"""
from __future__ import annotations
import argparse
import logging
import os
import threading

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)
_ENCRYPTED_PREFIX = "fernet:"
_warning_lock = threading.Lock()
_warned_without_key = False


def _get_fernet() -> Fernet | None:
    key = os.environ.get("CREDENTIALS_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("CREDENTIALS_ENCRYPTION_KEY must be a urlsafe base64 Fernet key") from exc


def _plaintext_credentials_allowed() -> bool:
    return os.environ.get("ALLOW_PLAINTEXT_CREDENTIALS", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def warn_if_encryption_key_missing() -> None:
    """Emit one process-wide warning when credential encryption is unavailable."""
    global _warned_without_key
    if _get_fernet() is not None:
        return
    with _warning_lock:
        if not _warned_without_key:
            if _plaintext_credentials_allowed():
                logger.warning(
                    "CREDENTIALS_ENCRYPTION_KEY is not set; plaintext credential storage is enabled"
                )
            else:
                logger.warning(
                    "CREDENTIALS_ENCRYPTION_KEY is not set; credential saves are blocked unless "
                    "ALLOW_PLAINTEXT_CREDENTIALS=true"
                )
            _warned_without_key = True


def encrypt_credential(value: str | None) -> str | None:
    if value is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        warn_if_encryption_key_missing()
        if not _plaintext_credentials_allowed():
            raise RuntimeError(
                "未配置 CREDENTIALS_ENCRYPTION_KEY，已拒绝保存渠道凭据。请运行 "
                "python -m publisher.credential_store --generate-key 并配置密钥；"
                "仅本地临时开发可设置 ALLOW_PLAINTEXT_CREDENTIALS=true。"
            )
        logger.warning("ALLOW_PLAINTEXT_CREDENTIALS is enabled; saving credentials without encryption")
        return value
    if value.startswith(_ENCRYPTED_PREFIX):
        fernet.decrypt(value[len(_ENCRYPTED_PREFIX):].encode("ascii"))
        return value
    return _ENCRYPTED_PREFIX + fernet.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_credential(value: str | None) -> str | None:
    if value is None or not value.startswith(_ENCRYPTED_PREFIX):
        return value
    fernet = _get_fernet()
    if fernet is None:
        raise RuntimeError(
            "CREDENTIALS_ENCRYPTION_KEY is required to read encrypted platform credentials"
        )
    token = value[len(_ENCRYPTED_PREFIX):]
    return fernet.decrypt(token.encode("ascii")).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Credential storage utilities")
    parser.add_argument("--generate-key", action="store_true", help="print a new Fernet key")
    args = parser.parse_args()
    if args.generate_key:
        print(Fernet.generate_key().decode("ascii"))
        return 0
    parser.error("select --generate-key")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


