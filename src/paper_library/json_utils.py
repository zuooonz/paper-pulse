"""Helpers for JSON values returned by ORM and legacy text-backed rows."""

from __future__ import annotations

import json
from typing import Any


def decode_json(value: Any, default: Any = None) -> Any:
    """Decode legacy JSON text while leaving native JSON values untouched."""
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value
