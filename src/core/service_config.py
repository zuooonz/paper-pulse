"""Resolve optional third party settings from environment or runtime.yaml."""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
_WARNED: set[tuple[str, str]] = set()


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda match: os.environ.get(match.group(1), ""),
            value,
        )
    return value


@lru_cache(maxsize=16)
def _read_runtime(path: str) -> dict[str, Any]:
    runtime_path = Path(path)
    if not runtime_path.exists():
        return {}
    try:
        with runtime_path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def runtime_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    if config_dir is not None:
        directory = Path(config_dir)
    else:
        workspace = Path(os.environ.get("WORKSPACE", "."))
        directory = workspace / os.environ.get("CONFIG_DIR", "config")
    runtime_path = directory / "runtime.yaml"
    if not runtime_path.exists():
        runtime_path = directory / "runtime.example.yaml"
    return _expand(_read_runtime(str(runtime_path)))


def app_timezone(config_dir: str | Path | None = None) -> str:
    """Return the configured local timezone, preferring APP_TIMEZONE."""
    return configured_value(
        "APP_TIMEZONE", "app", "timezone", default="Asia/Shanghai", config_dir=config_dir
    )


def storage_config(config_dir: str | Path | None = None) -> dict[str, str]:
    """Resolve storage roots from runtime config and the supported env overrides."""
    runtime = runtime_config(config_dir)
    configured = runtime.get("storage", {}) or {}
    if not isinstance(configured, dict):
        configured = {}
    root = os.environ.get("STORAGE_ROOT") or configured.get("root") or "data"
    paths = {
        "root": str(root),
        "pdf_root": os.environ.get("PDF_ROOT") or configured.get("pdf_root") or "pdf",
        "images_root": configured.get("images_root") or "images",
        "cache_root": configured.get("cache_root") or "cache",
        "temp_root": configured.get("temp_root") or "tmp",
        "output_root": os.environ.get("OUTPUT_ROOT") or configured.get("output_root") or "output",
    }
    return paths


def resolve_storage_path(name: str, workspace: str | Path = ".") -> Path:
    """Resolve a configured storage path; data roots are relative to storage.root."""
    workspace_path = Path(workspace).resolve()
    workspace_config_dir = workspace_path / os.environ.get("CONFIG_DIR", "config")
    config_dir = workspace_config_dir if workspace_config_dir.is_dir() else None
    paths = storage_config(config_dir)
    value = Path(paths[name])
    if name == "root":
        return value if value.is_absolute() else (workspace_path / value).resolve()
    if name == "output_root" or (name == "pdf_root" and os.environ.get("PDF_ROOT")):
        return value if value.is_absolute() else (workspace_path / value).resolve()
    root = Path(paths["root"])
    if root.is_absolute():
        return (root / value).resolve() if not value.is_absolute() else value
    root_path = (workspace_path / root).resolve()
    return (root_path / value).resolve() if not value.is_absolute() else value


def configured_value(
    env_name: str,
    *path: str,
    default: str = "",
    config_dir: str | Path | None = None,
) -> str:
    value = os.environ.get(env_name)
    if value is not None and value.strip():
        return value.strip()
    current: Any = runtime_config(config_dir)
    for part in path:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    if current is None:
        return default
    return str(current).strip() or default


def api_endpoint(
    env_name: str,
    endpoint_key: str,
    *,
    config_dir: str | Path | None = None,
) -> str:
    return configured_value(
        env_name,
        "api_endpoints",
        endpoint_key,
        config_dir=config_dir,
    ).rstrip("/")


def warn_missing(service: str, setting: str) -> None:
    key = (service, setting)
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning("跳过 %s：未配置 %s", service, setting)


def contact_user_agent() -> str:
    agent = "paper-pulse/1.0"
    contact = configured_value("CONTACT_EMAIL", "http", "contact_email")
    return f"{agent} ({contact})" if contact else agent
