"""Password hashing helpers for local accounts."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets


PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
MIN_PASSWORD_HASH_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32


def hash_password(password: str, iterations: int = MIN_PASSWORD_HASH_ITERATIONS) -> str:
    if iterations < MIN_PASSWORD_HASH_ITERATIONS:
        raise ValueError(f"iterations must be at least {MIN_PASSWORD_HASH_ITERATIONS}")
    if not isinstance(password, str) or not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=_HASH_BYTES)
    return "$".join((
        PASSWORD_HASH_SCHEME,
        str(iterations),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    ))


def _parse_password_hash(encoded: str) -> tuple[int, bytes, bytes] | None:
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != PASSWORD_HASH_SCHEME:
        return None
    try:
        iterations = int(parts[1])
        salt = base64.b64decode(parts[2], validate=True)
        expected = base64.b64decode(parts[3], validate=True)
    except (ValueError, binascii.Error):
        return None
    if iterations < MIN_PASSWORD_HASH_ITERATIONS or len(salt) < _SALT_BYTES or len(expected) != _HASH_BYTES:
        return None
    return iterations, salt, expected


def is_password_hash(encoded: str) -> bool:
    return isinstance(encoded, str) and encoded.startswith(f"{PASSWORD_HASH_SCHEME}$")


def verify_password(password: str, configured_value: str, *, allow_plaintext: bool = True) -> bool:
    if not isinstance(password, str) or not isinstance(configured_value, str):
        return False
    if is_password_hash(configured_value):
        parsed = _parse_password_hash(configured_value)
        if parsed is None:
            return False
        iterations, salt, expected = parsed
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    return allow_plaintext and hmac.compare_digest(
        password.encode("utf-8"), configured_value.encode("utf-8")
    )
