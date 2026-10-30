"""Outbound URL checks for downloads that may use externally supplied URLs."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlsplit


class UnsafeDownloadURL(ValueError):
    """Raised when a download URL or redirect target is not allowed."""


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SENSITIVE_HEADER_MARKERS = ("authorization", "cookie", "token", "api-key", "apikey")


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def validate_download_url(url: str, *, allow_private: bool | None = None) -> str:
    """Validate URL scheme and DNS results before an outbound request."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeDownloadURL("下载地址为空")
    value = url.strip()
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeDownloadURL("下载地址格式无效") from exc

    if scheme not in {"http", "https"}:
        raise UnsafeDownloadURL("下载地址只允许 HTTP 或 HTTPS")
    if not hostname or parsed.username is not None or parsed.password is not None:
        raise UnsafeDownloadURL("下载地址必须包含有效主机且不能包含用户凭据")

    host = hostname.rstrip(".")
    try:
        resolved = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            records = socket.getaddrinfo(
                host,
                port or (443 if scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeDownloadURL("下载地址主机无法解析") from exc
        resolved = set()
        for record in records:
            try:
                resolved.add(ipaddress.ip_address(record[4][0].split("%", 1)[0]))
            except ValueError as exc:
                raise UnsafeDownloadURL("下载地址解析到了无效 IP") from exc

    if not resolved:
        raise UnsafeDownloadURL("下载地址主机没有可用 IP")
    allow_private_networks = (
        _enabled(os.environ.get("ALLOW_PRIVATE_NETWORK_DOWNLOADS"))
        if allow_private is None
        else allow_private
    )
    if not allow_private_networks:
        if any(
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
            for address in resolved
        ):
            raise UnsafeDownloadURL("下载地址解析到了非公网 IP")
    return value


def safe_get(session, url: str, *, max_redirects: int = 5, **kwargs):
    """GET a URL with validation before each hop and redirects handled manually."""
    if max_redirects < 0:
        raise ValueError("max_redirects must be non-negative")
    kwargs.pop("allow_redirects", None)
    params = kwargs.pop("params", None)
    headers = dict(kwargs.pop("headers", {}) or {})
    current_url = url

    for redirect_count in range(max_redirects + 1):
        validate_download_url(current_url)
        response = session.get(
            current_url,
            params=params,
            headers=headers,
            allow_redirects=False,
            **kwargs,
        )
        params = None
        location = response.headers.get("Location")
        if response.status_code not in _REDIRECT_STATUSES or not location:
            return response
        if redirect_count >= max_redirects:
            response.close()
            raise UnsafeDownloadURL("下载重定向次数超过上限")

        next_url = urljoin(current_url, location)
        try:
            validate_download_url(next_url)
        except UnsafeDownloadURL:
            response.close()
            raise
        if _origin(next_url) != _origin(current_url):
            headers = {
                name: value
                for name, value in headers.items()
                if not any(marker in name.lower() for marker in _SENSITIVE_HEADER_MARKERS)
            }
        response.close()
        current_url = next_url

    raise UnsafeDownloadURL("下载重定向次数超过上限")
