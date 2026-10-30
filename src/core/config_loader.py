from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict

import yaml

from .domain_config import load_domain, render_domain_placeholders
from .taxonomy_config import load_taxonomy_config


def load_yaml_config(
    path: str | os.PathLike[str] | None = None,
    *,
    env_name: str | None = None,
    default_path: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    """Load a standalone YAML config using the project's env expansion rules."""
    configured_path = os.environ.get(env_name) if env_name else None
    selected_path = configured_path or path or default_path
    if selected_path is None:
        return {}
    config_path = Path(selected_path)
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            data = ConfigLoader._expand_environment(yaml.safe_load(stream) or {})
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


class ConfigLoader:
    def __init__(self, config_dir: Path) -> None:
        self.config_dir = config_dir

    def load_all(self) -> Dict[str, Any]:
        domain = self._load_domain()
        runtime_name = "runtime.yaml"
        if not (self.config_dir / runtime_name).exists() and (self.config_dir / "runtime.example.yaml").exists():
            runtime_name = "runtime.example.yaml"
        runtime = self._load_yaml(runtime_name)
        models = self._load_yaml("models.yaml")
        self._apply_llm_environment(runtime, models)
        return {
            "runtime": runtime,
            "models": models,
            "prompts": render_domain_placeholders(self._load_yaml("prompts.yaml"), domain=domain),
            "domain": domain,
            "taxonomy": load_taxonomy_config(self.config_dir),
        }

    def load_profile(self, profile_name: str) -> Dict[str, Any]:
        """Load a profile-specific config (e.g. profile_mkt.yaml).

        Returns empty dict for 'default' profile or if file doesn't exist.
        """
        if not profile_name or profile_name == "default":
            return {}
        path = self.config_dir / f"profile_{profile_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Profile config not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = self._expand_environment(yaml.safe_load(f) or {})
        if not isinstance(data, dict):
            raise ValueError(f"Invalid yaml format for profile: {path}")
        return render_domain_placeholders(data, domain=self._load_domain())

    def _load_domain(self) -> Dict[str, Any]:
        configured_path = os.environ.get("DOMAIN_CONFIG")
        if configured_path:
            return load_domain(config_path=configured_path)
        local_path = self.config_dir / "domain.yaml"
        if local_path.exists():
            return load_domain(config_path=local_path)
        return load_domain()

    def _load_yaml(self, name: str) -> Dict[str, Any]:
        path = self.config_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Missing config file: {path}")
        with path.open("r", encoding="utf-8") as f:
            data = self._expand_environment(yaml.safe_load(f) or {})
        if not isinstance(data, dict):
            raise ValueError(f"Invalid yaml format for {path}")
        return data

    @classmethod
    def _expand_environment(cls, value: Any) -> Any:
        """Expand ${NAME} in YAML values; missing variables become empty strings."""
        if isinstance(value, dict):
            return {key: cls._expand_environment(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._expand_environment(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                lambda match: os.environ.get(match.group(1), ""),
                value,
            )
        return value

    @staticmethod
    def _apply_llm_environment(runtime: Dict[str, Any], models: Dict[str, Any]) -> None:
        provider = runtime.setdefault("provider", {})
        for env_name, config_name in (
            ("LLM_BASE_URL", "base_url"),
            ("LLM_API_KEY", "api_key"),
            ("LLM_MODEL", "model"),
        ):
            value = os.environ.get(env_name)
            if value:
                provider[config_name] = value

        selected_model = os.environ.get("LLM_MODEL", "").strip()
        if not selected_model:
            return

        configured = models.setdefault("models", [])
        if not isinstance(configured, list):
            return
        template = next(
            (item for item in configured if isinstance(item, dict)),
            {"tier": "general", "quality": "standard", "cost": "standard"},
        )
        if not any(isinstance(item, dict) and item.get("id") == selected_model for item in configured):
            configured.append({**template, "id": selected_model})
        routing = models.setdefault("routing", {})
        if isinstance(routing, dict):
            routing["default_auto_sequence"] = [selected_model]

