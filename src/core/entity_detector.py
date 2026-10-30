"""从论文全文或元数据中识别设备厂商、产品及软件实体。"""
from __future__ import annotations

import copy
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "entities.yaml"


@lru_cache(maxsize=16)
def _load_entities_file(path: str, modified_ns: int) -> dict[str, Any]:
    del modified_ns
    with open(path, encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream) or {}
    if not isinstance(loaded, dict):
        raise ValueError("实体目录必须是 YAML 映射")
    return loaded


def load_entities_config(
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """加载实体配置。支持注入映射或路径，文件读取按修改时间缓存。"""
    if config is not None:
        return copy.deepcopy(dict(config))

    configured_path = config_path or os.environ.get("ENTITIES_CONFIG")
    path = Path(configured_path).expanduser() if configured_path else _DEFAULT_CONFIG_PATH
    path = path.resolve()
    stat = path.stat()
    return copy.deepcopy(_load_entities_file(str(path), stat.st_mtime_ns))


# Kept for command-line callers that present a default threshold; detection
# itself always reads the active configuration at call time.
MIN_SCORE_THRESHOLD = int(
    load_entities_config()["scoring"]["min_score"]
)


def get_target_entity_key(
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """返回目录中配置的目标实体 key；未配置时返回 None。"""
    loaded = load_entities_config(config_path=config_path, config=config)
    target = loaded.get("target")
    return target if isinstance(target, str) and target in loaded.get("entities", {}) else None


def get_target_label(
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """返回目标实体的显示名；目标为 null 时返回 None。"""
    loaded = load_entities_config(config_path=config_path, config=config)
    target = get_target_entity_key(config=config) if config is not None else get_target_entity_key(config_path)
    if target is None:
        return None
    entity = loaded["entities"][target]
    return str(entity.get("label") or target)


def _entity_catalog(config: Mapping[str, Any]) -> dict[str, dict[str, list[str]]]:
    entities = config.get("entities") or {}
    if not isinstance(entities, Mapping):
        return {}
    result: dict[str, dict[str, list[str]]] = {}
    for key, raw in entities.items():
        if not isinstance(raw, Mapping):
            continue
        result[str(key)] = {
            field: [str(value) for value in raw.get(field, []) or [] if str(value).strip()]
            for field in ("devices", "software", "company", "extra")
        }
    return result


def _display_name(key: str, config: Mapping[str, Any]) -> str:
    display_map = config.get("display_map") or {}
    entity = (config.get("entities") or {}).get(key, {})
    return str(display_map.get(key) or entity.get("label") or key)


_METHODS_HEADERS = [
    r"\b(?:2\.?\s*)?(?:Materials?\s*(?:and|&)\s*)?Methods?\b",
    r"\bExperimental\s+(?:Setup|Procedure|Design)\b",
    r"\b(?:MRI\s+)?Data\s+Acquisition\b",
    r"\bInstrumentation\b",
    r"\b(?:MRI\s+)?Recording\b",
    r"\bMeasurements?\b",
]

_METHODS_END_HEADERS = [
    r"\b(?:3\.?\s*)?Results?\b",
    r"\b(?:3\.?\s*)?Statistical\s+Analysis\b",
    r"\bData\s+Analysis\b",
    r"\bDiscussion\b",
]


def extract_methods_section(text: str) -> str:
    """从全文提取 Methods 段（Methods/Experimental → Results/Discussion 之间）。"""
    if not text:
        return ""
    best_start = len(text)
    for pattern in _METHODS_HEADERS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            best_start = min(best_start, match.start())
    if best_start >= len(text):
        return text

    search_from = best_start + 20
    best_end = len(text)
    for pattern in _METHODS_END_HEADERS:
        match = re.search(pattern, text[search_from:], re.IGNORECASE)
        if match:
            best_end = min(best_end, search_from + match.start())
    return text[best_start:best_end]


def _keyword_hit(keyword: str, text: str, text_lower: str) -> bool:
    """检查关键词命中，避免短词嵌入更长的普通单词。"""
    if keyword.lower() not in text_lower:
        return False
    if " " in keyword or "-" in keyword or len(keyword) <= 3:
        return True
    pattern = r"(?<![a-zA-Z0-9])(?i:" + re.escape(keyword) + r")(?![a-z])"
    return bool(re.search(pattern, text))


def _resolve_confusions(scores: dict[str, int], config: Mapping[str, Any]) -> None:
    """按配置把同义实体的分数合并到规范 key。"""
    confusions = config.get("confusions") or {}
    entities = config.get("entities") or {}
    display_to_key = {
        str(entity.get("label")): key
        for key, entity in entities.items()
        if isinstance(entity, Mapping) and entity.get("label")
    }
    for source, destination in confusions.items():
        target = destination if destination in entities else display_to_key.get(str(destination))
        if source in scores and target:
            scores[target] = scores.get(target, 0) + scores.pop(source)


def _context_bonus(text: str, key: str, terms: list[str], bonus: int) -> int:
    """实体关键词邻近设备或软件语境时加一次分。"""
    if not bonus or not terms:
        return 0
    context = r"\b(?:scanner|device|system|software|toolbox|package|manufacturer)\b"
    for term in terms:
        escaped = re.escape(term)
        pattern = rf"(?:{escaped}.{{0,100}}{context}|{context}.{{0,100}}{escaped})"
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return bonus
    return 0


def _score_text(text: str, config: Mapping[str, Any]) -> dict[str, int]:
    entities = _entity_catalog(config)
    scoring = config.get("scoring") or {}
    scores: dict[str, int] = {}
    text_lower = text.lower()
    for key, groups in entities.items():
        score = 0
        matched: list[str] = []
        for field in ("devices", "software", "company", "extra"):
            score_key = "device" if field == "devices" else field
            for keyword in groups[field]:
                if _keyword_hit(keyword, text, text_lower):
                    score += int(scoring[score_key])
                    matched.append(keyword)
        raw_entity = (config.get("entities") or {}).get(key, {})
        if isinstance(raw_entity, Mapping):
            for rule in raw_entity.get("conditional_matches", []) or []:
                if not isinstance(rule, Mapping):
                    continue
                terms = [str(term) for term in rule.get("all_terms", []) or []]
                if not terms or not all(
                    re.search(
                        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
                        text,
                        re.IGNORECASE,
                    )
                    for term in terms
                ):
                    continue
                field = str(rule.get("field") or "software")
                if field in {"devices", "software", "company", "extra"}:
                    score_key = "device" if field == "devices" else field
                    score += int(scoring[score_key])
                    matched.append(" ".join(terms))
        if score:
            score += _context_bonus(text, key, matched, int(scoring["context_bonus"]))
            scores[key] = score
    _resolve_confusions(scores, config)
    return scores


def _confidence(score: int, scoring: Mapping[str, Any]) -> str:
    if score >= int(scoring["high_confidence"]):
        return "high"
    if score >= int(scoring["medium_confidence"]):
        return "medium"
    return "low"


def _resolve_final(
    scores: dict[str, int], config: Mapping[str, Any]
) -> tuple[Optional[str], Optional[str]]:
    if not scores:
        return None, None
    scoring = config.get("scoring") or {}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_key, top_score = ranked[0]
    if top_score < int(scoring["min_score"]):
        return None, "low"
    if len(ranked) > 1 and ranked[1][1] >= top_score * float(scoring["ambiguity_ratio"]):
        return _display_name(top_key, config), "ambiguous"
    return _display_name(top_key, config), _confidence(top_score, scoring)


def detect_entity(
    text: str,
    *,
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[Optional[str], Optional[str], dict[str, int]]:
    """识别文本中的实体并按配置评分，返回显示名、置信度和 key 得分。"""
    if not text or len(text.strip()) < 50:
        return None, None, {}
    loaded = load_entities_config(config_path=config_path, config=config)
    custom_patterns = loaded.get("custom_built_patterns") or []
    for pattern in custom_patterns:
        if re.search(str(pattern), text, re.IGNORECASE):
            label = str(
                (loaded.get("display_map") or {}).get("custom_built")
                or loaded.get("custom_built_label")
            )
            return label, "medium", {"custom_built": int(loaded["scoring"]["custom_built"])}
    scores = _score_text(text, loaded)
    if not scores:
        return None, None, {}
    label, confidence = _resolve_final(scores, loaded)
    return label, confidence, scores


def _add_context_bonus(
    text: str, scores: dict[str, int], config: Mapping[str, Any] | None = None
) -> None:
    """为出现在设备或软件语境中的实体增加配置分数。"""
    loaded = load_entities_config(config=config)
    scoring = loaded.get("scoring") or {}
    bonus = int(scoring["context_bonus"])
    catalog = _entity_catalog(loaded)
    for key in list(scores):
        terms = [term for group in catalog.get(key, {}).values() for term in group]
        scores[key] += _context_bonus(text, key, terms, bonus)


def detect_entity_with_paper(
    paper,
    *,
    config_path: str | os.PathLike[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[Optional[str], Optional[str], dict[str, int]]:
    """针对 Paper 对象识别实体，优先使用全文和方法章节，随后检查摘要。"""
    all_scores: dict[str, int] = {}

    def merge(source_scores: Mapping[str, int]) -> None:
        for key, value in source_scores.items():
            all_scores[key] = max(all_scores.get(key, 0), value)

    if paper.extracted_text:
        text = paper.extracted_text
        _, _, scores = detect_entity(text, config_path=config_path, config=config)
        merge(scores)
        methods = extract_methods_section(text)
        if methods and len(methods) > 200:
            _, _, methods_scores = detect_entity(methods, config_path=config_path, config=config)
            merge(methods_scores)
        loaded = load_entities_config(config_path=config_path, config=config)
        label, confidence = _resolve_final(all_scores, loaded)
        if label:
            return label, confidence, all_scores

    if paper.abstract:
        _, _, abstract_scores = detect_entity(paper.abstract, config_path=config_path, config=config)
        merge(abstract_scores)
        loaded = load_entities_config(config_path=config_path, config=config)
        label, confidence = _resolve_final(all_scores, loaded)
        if label:
            return label, confidence, all_scores
    return None, None, all_scores


