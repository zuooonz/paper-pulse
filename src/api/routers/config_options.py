"""Read-only application option catalogs sourced from configuration."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from core.config_loader import ConfigLoader
from core.taxonomy_config import dimension_items
from ..config import settings
from ..dependencies import get_current_user


router = APIRouter(prefix="/api/config", tags=["configuration"])

def build_config_options(config: dict[str, Any]) -> dict[str, Any]:
    prompts = config.get("prompts") or {}
    models = config.get("models") or {}
    taxonomy = config.get("taxonomy") or {}
    domain = config.get("domain") or {}
    generation = domain.get("generation") if isinstance(domain, dict) else {}
    theme_values = prompts.get("themes")
    if not isinstance(theme_values, list):
        theme_values = list((prompts.get("theme_templates") or {}).keys())
    theme_options = [
        {"value": str(value), "label": str(value)}
        for value in theme_values if str(value).strip()
    ]

    configured_models = models.get("models") or []
    model_values = [
        str(item.get("id")) for item in configured_models
        if isinstance(item, dict) and item.get("id")
    ]
    model_options = [{"value": "auto", "label": "自动选择 (auto)"}]
    model_options.extend({"value": value, "label": value} for value in dict.fromkeys(model_values))

    dimensions = taxonomy.get("dimensions") if isinstance(taxonomy, dict) else {}
    labels = {
        str(key): str(value.get("label") or key)
        for key, value in dimensions.items()
        if isinstance(value, dict)
    } if isinstance(dimensions, dict) else {}
    return {
        "themes": theme_options,
        "models": model_options,
        "multimodal_timings": [
            {"value": str(item.get("value") or item["label"]), "label": str(item["label"])}
            for item in dimension_items(taxonomy, "multimodal_timing")
            if isinstance(item, dict) and item.get("key") and item.get("label")
        ],
        "taxonomy": taxonomy,
        "taxonomy_labels": labels,
        "generation": {
            "require_target_entity": bool(
                generation.get("require_target_entity", True)
                if isinstance(generation, dict)
                else True
            ),
        },
    }


@router.get("/options")
def get_config_options(user: dict = Depends(get_current_user)):
    """Return read-only application option catalogs."""
    config_dir = Path(settings.workspace) / settings.config_dir
    config = ConfigLoader(config_dir).load_all()
    return build_config_options(config)
