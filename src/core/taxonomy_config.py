"""Load and normalize the configurable paper taxonomy."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from paper_library.json_utils import decode_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_taxonomy_config(config_dir: str | Path | None = None) -> dict[str, Any]:
    """Load taxonomy.yaml, honoring TAXONOMY_CONFIG and a supplied config dir."""
    override = os.environ.get("TAXONOMY_CONFIG")
    if override:
        path = Path(override)
    elif config_dir is not None:
        path = Path(config_dir) / "taxonomy.yaml"
    else:
        workspace = Path(os.environ.get("WORKSPACE", PROJECT_ROOT))
        configured_dir = Path(os.environ.get("CONFIG_DIR", "config"))
        if not configured_dir.is_absolute():
            configured_dir = workspace / configured_dir
        path = configured_dir / "taxonomy.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"dimensions": {}, "multimodal_timings": []}
    if not isinstance(data, dict):
        return {"dimensions": {}, "multimodal_timings": []}
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        data["dimensions"] = {}
    return data


def dimension_items(taxonomy: dict[str, Any], dimension: str) -> list[dict[str, Any]]:
    dimensions = taxonomy.get("dimensions")
    definition = dimensions.get(dimension) if isinstance(dimensions, dict) else None
    if dimension == "multimodal_timing" and not isinstance(definition, dict):
        items = taxonomy.get("multimodal_timings")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict) and item.get("label")]
    if not isinstance(definition, dict):
        return []
    items = definition.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("label")]


def dimension_aliases(taxonomy: dict[str, Any], dimension: str) -> dict[str, tuple[str, ...]]:
    """Return canonical labels mapped to configured aliases, including labels."""
    aliases: dict[str, tuple[str, ...]] = {}
    for item in dimension_items(taxonomy, dimension):
        label = str(item.get("value") or item["label"]).strip()
        values = item.get("aliases") or []
        if isinstance(values, str):
            values = [values]
        cleaned = [label, str(item["label"]).strip()]
        cleaned.extend(str(value).strip() for value in values if str(value).strip())
        aliases[label] = tuple(dict.fromkeys(cleaned))
    definition = (taxonomy.get("dimensions") or {}).get(dimension, {})
    extra_aliases = definition.get("aliases", {}) if isinstance(definition, dict) else {}
    if isinstance(extra_aliases, dict):
        for label, values in extra_aliases.items():
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                aliases[str(label)] = tuple(dict.fromkeys([str(label), *(str(value) for value in values)]))
    return aliases


def dimension_patterns(taxonomy: dict[str, Any], dimension: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result = []
    for item in dimension_items(taxonomy, dimension):
        patterns = item.get("patterns") or []
        if isinstance(patterns, str):
            patterns = [patterns]
        result.append((str(item.get("value") or item["label"]), tuple(str(pattern) for pattern in patterns)))
    return tuple(result)


def dimension_groups(taxonomy: dict[str, Any], dimension: str) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for item in dimension_items(taxonomy, dimension):
        group = str(item.get("group") or "").strip()
        if group:
            grouped.setdefault(group, []).append(str(item.get("value") or item["label"]))
    definition = (taxonomy.get("dimensions") or {}).get(dimension, {})
    explicit_groups = definition.get("groups", {}) if isinstance(definition, dict) else {}
    if isinstance(explicit_groups, dict):
        for label, values in explicit_groups.items():
            if isinstance(values, list):
                grouped[str(label)] = [str(value) for value in values]
    return {label: tuple(values) for label, values in grouped.items()}


def dimension_label_map(taxonomy: dict[str, Any], dimension: str) -> dict[str, str]:
    return {
        str(item.get("value") or item["label"]): str(item["label"])
        for item in dimension_items(taxonomy, dimension)
    }


def canonical_taxonomy_value(
    taxonomy: dict[str, Any], dimension: str, value: Any,
) -> str | None:
    """Resolve an exact canonical label or alias to its configured label."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().casefold()
    for label, aliases in dimension_aliases(taxonomy, dimension).items():
        if any(alias.casefold() == candidate for alias in aliases):
            return label
    return None


def observed_taxonomy_values(value: Any, aliases: dict[str, tuple[str, ...]]) -> list[str]:
    """Keep values found in data, normalizing exact aliases when configured."""
    if isinstance(value, str):
        parsed = decode_json(value, value)
        if parsed is not value:
            value = parsed
    if isinstance(value, dict):
        if "techniques" in value:
            value = value.get("techniques")
        else:
            value = list(value.values())

    flattened: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            text = item.strip()
            if text:
                flattened.append(text)
        elif isinstance(item, dict):
            if "name" in item or "method" in item:
                collect(item.get("name") or item.get("method"))
            elif "techniques" in item:
                collect(item.get("techniques"))
        elif isinstance(item, (tuple, list, set)):
            for nested in item:
                collect(nested)

    collect(value)
    normalized: list[str] = []
    alias_lookup = {
        alias.casefold(): label
        for label, terms in aliases.items()
        for alias in terms
    }
    for text in flattened:
        label = alias_lookup.get(text.casefold(), text)
        if label not in normalized:
            normalized.append(label)
    return normalized


def taxonomy_options(
    counts: dict[str, int], taxonomy: dict[str, Any], dimension: str,
    *, custom_group: str = "其他/自定义",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return configured values with data and observed custom values, with groups."""
    items = dimension_items(taxonomy, dimension)
    aliases = dimension_aliases(taxonomy, dimension)
    grouped = dimension_groups(taxonomy, dimension)
    item_labels = [str(item.get("value") or item["label"]) for item in items]
    options: list[dict[str, Any]] = []
    for label in item_labels:
        count = counts.get(label, 0)
        if count > 0:
            item = next(item for item in items if str(item.get("value") or item["label"]) == label)
            options.append({"value": label, "label": str(item["label"]), "count": count, "custom": False,
                            **({"group": item["group"]} if item.get("group") else {})})

    known_values = {alias.casefold() for values in aliases.values() for alias in values}
    custom = sorted(
        (value for value, count in counts.items() if count > 0 and value.casefold() not in known_values),
        key=str.casefold,
    )
    options.extend({"value": value, "label": value, "count": counts[value], "custom": True,
                    "group": custom_group} for value in custom)

    groups = []
    for label, values in grouped.items():
        available = [value for value in values if any(option["value"] == value for option in options)]
        if available:
            groups.append({"label": label, "options": available})
    if custom:
        groups.append({"label": custom_group, "options": custom})
    return options, groups
