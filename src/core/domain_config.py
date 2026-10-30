"""Load the active research-domain package and safely render its prompt values."""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "domain.yaml"
_PLACEHOLDER_RE = re.compile(
    r"\{(domain_name|domain_short_name|domain_intro|required_terms|target_label|strengths_section_title)\}"
)
_GENERIC_STYLE_FALLBACKS = {
    "professional": "学术风格：采用与论文主题相符的示意动画、数据可视化或研究场景画面。",
    "popular_science": "科普风格：用简洁的动画图解、清楚的标签和易理解的视觉表达。",
    "news": "新闻播报风格：用动态标题、数据图表和与主题相关的现场画面呈现信息。",
}


@lru_cache(maxsize=16)
def _load_domain_file(path: str, modified_ns: int) -> dict[str, Any]:
    del modified_ns
    with open(path, encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError("领域配置必须是 YAML 映射")
    return loaded


def load_domain(
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the domain package; accepts an injected mapping or path and caches files."""
    if config is not None:
        return copy.deepcopy(dict(config))
    configured_path = config_path or os.environ.get("DOMAIN_CONFIG")
    path = Path(configured_path).expanduser() if configured_path else _DEFAULT_CONFIG_PATH
    path = path.resolve()
    stat = path.stat()
    return copy.deepcopy(_load_domain_file(str(path), stat.st_mtime_ns))


def render_domain_placeholders(
    value: Any,
    *,
    domain: Mapping[str, Any] | None = None,
    target_label: str | None = None,
) -> Any:
    """Replace supported placeholders recursively; leave unrelated braces untouched."""
    loaded = load_domain(config=domain) if domain is not None else load_domain()
    prompt = loaded.get("prompt") if isinstance(loaded.get("prompt"), Mapping) else {}
    replacements = {
        "domain_name": str(loaded.get("name") or loaded.get("short_name") or "研究领域"),
        "domain_short_name": str(loaded.get("short_name") or loaded.get("name") or "研究领域"),
        "domain_intro": str(prompt.get("domain_intro") or ""),
        "required_terms": "、".join(str(term) for term in prompt.get("required_terms", []) if str(term).strip())
        or "无固定必需术语",
        "target_label": target_label or "",
        "strengths_section_title": str(prompt.get("strengths_section_title") or "方法适用条件分析"),
    }

    def render(item: Any) -> Any:
        if isinstance(item, str):
            return _PLACEHOLDER_RE.sub(lambda match: replacements[match.group(1)], item)
        if isinstance(item, list):
            return [render(child) for child in item]
        if isinstance(item, tuple):
            return tuple(render(child) for child in item)
        if isinstance(item, dict):
            return {
                render(key) if isinstance(key, str) else key: render(child)
                for key, child in item.items()
            }
        return item

    return render(value)


def domain_tags(config: Mapping[str, Any] | None = None) -> list[str]:
    loaded = load_domain(config=config) if config is not None else load_domain()
    tags = loaded.get("default_tags") or []
    return [str(tag).strip() for tag in tags if str(tag).strip()]


def generation_requires_target_entity(
    domain: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether generation is limited to papers matching the target entity."""
    loaded = load_domain(config=domain) if domain is not None else load_domain()
    generation = loaded.get("generation")
    if not isinstance(generation, Mapping):
        return True
    return bool(generation.get("require_target_entity", True))


def paper_matches_generation_scope(
    entity_label: str | None,
    target_label: str | None,
    *,
    domain: Mapping[str, Any] | None = None,
) -> bool:
    """Check if a paper can be generated under the configured entity scope."""
    if not generation_requires_target_entity(domain):
        return True
    return bool(target_label and entity_label == target_label)


def video_style_prompt(
    style: str,
    domain: Mapping[str, Any] | None = None,
) -> str:
    """Read a visual style prompt from the domain package with a neutral fallback."""
    loaded = load_domain(config=domain) if domain is not None else load_domain()
    video_style = loaded.get("video_style")
    prompts = video_style.get("prompts") if isinstance(video_style, Mapping) else None
    if isinstance(prompts, Mapping):
        prompt = prompts.get(style)
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return _GENERIC_STYLE_FALLBACKS.get(
        style,
        _GENERIC_STYLE_FALLBACKS["professional"],
    )
