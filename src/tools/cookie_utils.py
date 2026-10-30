"""Shared helpers for browser cookie values."""

from __future__ import annotations


def format_cookie_string(cookies: list[dict]) -> str:
    """Join Playwright cookies into the standard request Cookie header value."""
    return "; ".join(
        f"{cookie.get('name', '')}={cookie.get('value', '')}"
        for cookie in cookies
        if cookie.get("name")
    )
