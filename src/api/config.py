import logging
from pathlib import Path
from typing import Any
import os
import re

import yaml
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)


class RuntimeYamlSettingsSource(PydanticBaseSettingsSource):
    """Load application settings from config/runtime.yaml."""

    def get_field_value(self, field, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    @staticmethod
    def _expand(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: RuntimeYamlSettingsSource._expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [RuntimeYamlSettingsSource._expand(item) for item in value]
        if isinstance(value, str):
            return re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                lambda match: os.environ.get(match.group(1), ""),
                value,
            )
        return value

    def __call__(self) -> dict[str, Any]:
        model_fields = getattr(self.settings_cls, "model_fields", {})
        workspace_field = model_fields.get("workspace")
        config_dir_field = model_fields.get("config_dir")
        workspace = os.environ.get("WORKSPACE") or (getattr(workspace_field, "default", ".") if workspace_field else ".")
        config_dir = os.environ.get("CONFIG_DIR") or (getattr(config_dir_field, "default", "config") if config_dir_field else "config")
        runtime_path = Path(workspace) / config_dir / "runtime.yaml"
        if not runtime_path.exists():
            return {}

        try:
            with runtime_path.open("r", encoding="utf-8") as stream:
                data = self._expand(yaml.safe_load(stream) or {})
        except Exception:
            logger.warning("运行配置读取失败，使用默认配置", exc_info=True)
            return {}

        mapping: dict[str, Any] = {}
        auth = data.get("auth", {}) or {}
        for yaml_name, field_name in (
            ("jwt_secret", "jwt_secret"),
            ("jwt_algorithm", "jwt_algorithm"),
            ("jwt_expire_days", "jwt_expire_days"),
            ("allow_plaintext_passwords", "allow_plaintext_passwords"),
            ("local_users", "local_users"),
        ):
            if yaml_name in auth:
                mapping[field_name] = auth[yaml_name]

        provider = data.get("provider", {}) or {}
        for yaml_name, field_name in (
            ("base_url", "llm_base_url"),
            ("api_key", "llm_api_key"),
            ("model", "llm_model"),
        ):
            if yaml_name in provider:
                mapping[field_name] = provider[yaml_name]

        pdf_download = data.get("pdf_download", {}) or {}
        for yaml_name, field_name in (
            ("elsevier_api_key", "elsevier_api_key"),
            ("ieee_api_key", "ieee_api_key"),
            ("wiley_tdm_token", "wiley_tdm_token"),
            ("unpaywall_email", "unpaywall_email"),
        ):
            if yaml_name in pdf_download:
                mapping[field_name] = pdf_download[yaml_name]

        extractor = data.get("extractor", {}) or {}
        mineru = extractor.get("mineru", {}) or {}
        for yaml_name, field_name in (("api_token", "mineru_api_token"), ("base_url", "mineru_base_url")):
            if yaml_name in mineru:
                mapping[field_name] = mineru[yaml_name]

        for section, yaml_name, field_name in (
            ("semantic_scholar", "api_key", "semantic_scholar_api_key"),
            ("springer", "api_key", "springer_api_key"),
            ("crossref", "mailto", "crossref_mailto"),
            ("ncbi", "api_key", "ncbi_api_key"),
            ("ncbi", "email", "ncbi_email"),
            ("maps", "amap_api_key", "amap_api_key"),
            ("maps", "baidu_map_ak", "baidu_map_ak"),
            ("http", "contact_email", "contact_email"),
        ):
            values = data.get(section, {}) or {}
            if yaml_name in values:
                mapping[field_name] = values[yaml_name]

        endpoints = data.get("api_endpoints", {}) or {}
        endpoint_fields = {
            "elsevier_base_url": "elsevier_base_url",
            "ieee_base_url": "ieee_base_url",
            "wiley_base_url": "wiley_base_url",
            "springer_base_url": "springer_base_url",
            "semantic_scholar_base_url": "semantic_scholar_base_url",
            "unpaywall_base_url": "unpaywall_base_url",
            "crossref_base_url": "crossref_base_url",
            "ncbi_base_url": "ncbi_base_url",
            "amap_base_url": "amap_base_url",
            "baidu_map_base_url": "baidu_map_base_url",
        }
        for yaml_name, field_name in endpoint_fields.items():
            if yaml_name in endpoints:
                mapping[field_name] = endpoints[yaml_name]

        dingtalk = data.get("dingtalk", {}) or {}
        for yaml_name, field_name in (
            ("enabled", "dingtalk_enabled"),
            ("corp_id", "dingtalk_corp_id"),
            ("client_id", "dingtalk_client_id"),
            ("client_secret", "dingtalk_client_secret"),
            ("agent_id", "dingtalk_agent_id"),
        ):
            if yaml_name in dingtalk:
                mapping[field_name] = dingtalk[yaml_name]
        return mapping


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30
    jwt_token_version: int = 1
    allow_plaintext_passwords: bool = False
    local_users: dict[str, dict[str, str]] = Field(default_factory=dict)
    local_admin_password: str = ""
    local_reviewer_password: str = ""
    local_viewer_password: str = ""
    local_limited_password: str = ""
    workspace: str = "."
    config_dir: str = "config"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "example-chat-model"

    elsevier_api_key: str = ""
    ieee_api_key: str = ""
    wiley_tdm_token: str = ""
    unpaywall_email: str = ""
    semantic_scholar_api_key: str = ""
    springer_api_key: str = ""
    crossref_mailto: str = ""
    ncbi_api_key: str = ""
    ncbi_email: str = ""
    mineru_api_token: str = ""
    mineru_base_url: str = ""
    amap_api_key: str = ""
    baidu_map_ak: str = ""
    contact_email: str = ""

    elsevier_base_url: str = ""
    ieee_base_url: str = ""
    wiley_base_url: str = ""
    springer_base_url: str = ""
    semantic_scholar_base_url: str = ""
    unpaywall_base_url: str = ""
    crossref_base_url: str = ""
    ncbi_base_url: str = ""
    amap_base_url: str = ""
    baidu_map_base_url: str = ""

    dingtalk_enabled: bool = False
    dingtalk_corp_id: str = ""
    dingtalk_client_id: str = ""
    dingtalk_client_secret: str = ""
    dingtalk_agent_id: str = ""

    @model_validator(mode="after")
    def _derive_local_users(self):
        """Allow simple Compose deployments to set LOCAL_*_PASSWORD only."""
        if self.local_users:
            return self
        candidates = (
            ("admin", "管理员", "admin", self.local_admin_password),
            ("reviewer", "审核员", "reviewer", self.local_reviewer_password),
            ("viewer", "查看者", "viewer", self.local_viewer_password),
            ("limited", "受限用户", "limited", self.local_limited_password),
        )
        users = {
            username: {"password": password, "name": name, "role": role}
            for username, name, role, password in candidates
            if password
        }
        if users:
            self.local_users = users
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            RuntimeYamlSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


settings = Settings()
