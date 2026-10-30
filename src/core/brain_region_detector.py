"""从论文摘要/全文中提取脑区信息。

基于预定义关键词词典，从文本中识别并返回标准化脑区列表。
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# 脑区关键词定义：{ 标准名: { "keywords": [...], "weight": int } }
# weight: 3=核心皮层区域, 2=皮层下/细分结构, 1=全局标记
# ---------------------------------------------------------------------------

BRAIN_REGIONS: dict[str, dict] = {
    "prefrontal cortex": {
        "keywords": [
            "prefrontal cortex", "dorsolateral prefrontal cortex",
            "ventromedial prefrontal", "orbitofrontal",
        ],
        "weight": 3,
    },
    "motor cortex": {
        "keywords": [
            "motor cortex", "primary motor cortex",
            "supplementary motor area", "premotor cortex", "premotor",
        ],
        "weight": 3,
    },
    "somatosensory cortex": {
        "keywords": [
            "somatosensory cortex", "primary somatosensory",
            "postcentral gyrus", "postcentral",
        ],
        "weight": 3,
    },
    "superior temporal gyrus": {
        "keywords": ["superior temporal gyrus", "superior temporal"],
        "weight": 3,
    },
    "middle temporal gyrus": {
        "keywords": ["middle temporal gyrus", "middle temporal"],
        "weight": 3,
    },
    "inferior temporal gyrus": {
        "keywords": ["inferior temporal gyrus", "inferior temporal"],
        "weight": 3,
    },
    "auditory cortex": {
        "keywords": ["auditory cortex"],
        "weight": 3,
    },
    "parietal cortex": {
        "keywords": [
            "parietal cortex", "superior parietal", "inferior parietal",
        ],
        "weight": 3,
    },
    "occipital cortex": {
        "keywords": [
            "occipital cortex", "visual cortex", "fusiform gyrus", "fusiform",
        ],
        "weight": 3,
    },
    "cingulate cortex": {
        "keywords": [
            "cingulate cortex", "anterior cingulate", "posterior cingulate",
        ],
        "weight": 3,
    },
    "insula": {
        "keywords": ["insula", "anterior insula"],
        "weight": 2,
    },
    "hippocampus": {
        "keywords": ["hippocampus", "hippocampal"],
        "weight": 2,
    },
    "entorhinal cortex": {
        "keywords": ["entorhinal cortex", "entorhinal", "parahippocampal"],
        "weight": 2,
    },
    "amygdala": {
        "keywords": ["amygdala"],
        "weight": 2,
    },
    "basal ganglia": {
        "keywords": ["basal ganglia", "striatum", "caudate", "putamen"],
        "weight": 2,
    },
    "thalamus": {
        "keywords": ["thalamus", "thalamic"],
        "weight": 2,
    },
    "cerebellum": {
        "keywords": ["cerebellum", "cerebellar"],
        "weight": 2,
    },
    "inferior frontal gyrus": {
        "keywords": ["inferior frontal gyrus", "inferior frontal"],
        "weight": 2,
    },
    "middle frontal gyrus": {
        "keywords": ["middle frontal gyrus", "middle frontal"],
        "weight": 2,
    },
    "superior frontal gyrus": {
        "keywords": ["superior frontal gyrus", "superior frontal"],
        "weight": 2,
    },
    "temporoparietal junction": {
        "keywords": ["temporoparietal junction"],
        "weight": 2,
    },
    "whole-head": {
        "keywords": [
            "whole-head", "whole head", "whole-brain", "whole brain",
            "bilateral",
        ],
        "weight": 1,
    },
}

# ---------------------------------------------------------------------------
# 缩写映射：{ 缩写: 标准脑区名 }
# 短缩写（≤3字符）需要严格匹配：必须全大写 + 上下文验证
# ---------------------------------------------------------------------------

ABBREVIATIONS: dict[str, str] = {
    "PFC": "prefrontal cortex",
    "DLPFC": "prefrontal cortex",
    "VMPFC": "prefrontal cortex",
    "OFC": "prefrontal cortex",
    "M1": "motor cortex",
    "SMA": "motor cortex",
    "S1": "somatosensory cortex",
    "STG": "superior temporal gyrus",
    "MTG": "middle temporal gyrus",
    "ITG": "inferior temporal gyrus",
    "IPL": "parietal cortex",
    "V1": "occipital cortex",
    "ACC": "cingulate cortex",
    "PCC": "cingulate cortex",
    "IFG": "inferior frontal gyrus",
    "MFG": "middle frontal gyrus",
    "SFG": "superior frontal gyrus",
    "TPJ": "temporoparietal junction",
    "EC": "entorhinal cortex",
}

# ---------------------------------------------------------------------------
# 上下文词：用于验证短缩写是否真正指代脑区
# ---------------------------------------------------------------------------

CONTEXT_WORDS = [
    "cortex", "cortical", "region", "area", "gyrus", "lobe", "sulcus",
    "activation", "activation", "hemodynamic", "oxygenation", "blood flow",
    "neural", "neuronal", "brain", "cerebral", "functional",
    "prefrontal", "motor", "somatosensory", "temporal", "parietal",
    "occipital", "cingulate", "frontal",
]

# ---------------------------------------------------------------------------
# 核心提取函数
# ---------------------------------------------------------------------------


def _keyword_match(keyword: str, text: str, text_lower: str) -> bool:
    """检查关键词是否在文本中命中（单词级边界匹配）。"""
    kw_lower = keyword.lower()
    if kw_lower not in text_lower:
        return False

    # 含空格/连字符的直接子串匹配
    if " " in keyword or "-" in keyword:
        return True

    # 单词边界检查
    pattern = r"(?<![a-zA-Z0-9])" + re.escape(keyword) + r"(?![a-zA-Z0-9])"
    return bool(re.search(pattern, text, re.IGNORECASE))


def _abbreviation_match(abbrev: str, text: str) -> bool:
    """严格匹配短缩写：必须全大写 + 上下文词验证。

    Args:
        abbrev: 缩写关键词（如 "PFC", "M1", "ACC"）
        text: 原文（非 lowercase）

    Returns:
        True 如果缩写确实指代脑区
    """
    # 必须全大写出现
    pattern = r"(?<![a-zA-Z])" + re.escape(abbrev) + r"(?![a-zA-Z])"
    match = re.search(pattern, text)
    if not match:
        return False

    # 检查附近 ±50 字符内是否有上下文词
    start = max(0, match.start() - 50)
    end = min(len(text), match.end() + 50)
    context = text[start:end].lower()

    for ctx_word in CONTEXT_WORDS:
        if ctx_word in context:
            return True

    return False


def extract_brain_regions(text: str) -> list[str]:
    """从论文文本中提取脑区列表。

    Args:
        text: 论文摘要或全文

    Returns:
        标准脑区名列表（按词典顺序），如 ["prefrontal cortex", "motor cortex", "bilateral"]
    """
    if not text or len(text.strip()) < 30:
        return []

    text_lower = text.lower()
    found: list[str] = []

    for region_name, region_data in BRAIN_REGIONS.items():
        matched = False

        # 检查全名关键词
        for keyword in region_data["keywords"]:
            if _keyword_match(keyword, text, text_lower):
                matched = True
                break

        # 检查缩写（如果全名未命中）
        if not matched:
            for abbrev, mapped_region in ABBREVIATIONS.items():
                if mapped_region == region_name:
                    if _abbreviation_match(abbrev, text):
                        matched = True
                        break

        if matched:
            found.append(region_name)

    return found


def extract_brain_regions_with_paper(paper) -> list[str]:
    """从 Paper ORM 对象中提取脑区，优先全文 → 摘要。

    Args:
        paper: Paper ORM 对象

    Returns:
        标准脑区名列表
    """
    # 优先全文
    if paper.extracted_text and len(paper.extracted_text) > 100:
        result = extract_brain_regions(paper.extracted_text)
        if result:
            return result

    # 回退到摘要
    if paper.abstract and len(paper.abstract) > 50:
        return extract_brain_regions(paper.abstract)

    return []
