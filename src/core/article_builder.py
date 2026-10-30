from __future__ import annotations

from typing import Any, Dict, List

from core.domain_config import load_domain, render_domain_placeholders


def _safe(rec: Dict[str, Any], key: str) -> str:
    val = rec.get(key)
    return "" if val is None else str(val).strip()


def _is_missing_placeholder(value: str) -> bool:
    token = value.strip().lower()
    if not token:
        return True
    return token in {"-", "--", "---", "/", "na", "n/a", "none", "null", "nan", "无", "未知", "未提供"}


def _pick_first_nonempty(rec: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        val = _safe(rec, key)
        if val and not _is_missing_placeholder(val):
            return val
    return ""


def _format_metadata_block(rec: Dict[str, Any]) -> str:
    """Build structured metadata from Excel columns or direct extraction."""
    fields = [
        ("论文编号", _safe(rec, "论文编号")),
        ("线上核准标题", _safe(rec, "API权威标题")),
        ("线上核准期刊", _safe(rec, "API权威期刊")),
        ("线上核准日期", _safe(rec, "API发表日期")),
        ("收录英文标题", _safe(rec, "英文标题")),
        ("收录期刊名称", _safe(rec, "期刊名称")),
        ("影响因子(IF)", _safe(rec, "IF")),
        ("JCR分区", _safe(rec, "JCR分区")),
        ("DOI", _safe(rec, "DOI")),
        ("PMID", _safe(rec, "PMID")),
        ("预构建APA引用", _safe(rec, "apa_citation")),
        ("通讯作者", _safe(rec, "通讯作者")),
        ("第一作者", _safe(rec, "第一作者")),
    ]
    lines = []
    for label, value in fields:
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def _format_device_context_block(rec: Dict[str, Any], target_label: str | None = None) -> str:
    """Build structured device context from Excel columns to avoid loss by text truncation."""
    fields = [
        ("设备引用信息", _pick_first_nonempty(rec, ["设备引用信息", "设备引用信息/说明"])),
        ("使用设备", _pick_first_nonempty(rec, ["使用设备", "设备名称", "设备"])),
        ("通道数", _pick_first_nonempty(rec, ["通道数"])),
        ("是否使用目标软件", _pick_first_nonempty(rec, ["是否使用目标软件", "uses_target_software"])),
    ]
    lines = []
    for label, value in fields:
        if label == "使用设备" and target_label and value == target_label:
            continue
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def _build_structure_guidance(prompts_cfg: Dict[str, Any]) -> str:
    """Build article structure guidance from config."""
    structure = prompts_cfg.get("article_structure", [])
    if not structure:
        return ""
    lines = ["请严格按以下结构输出中文推文："]
    for item in structure:
        if isinstance(item, dict):
            section = item.get("section", "")
            fmt = item.get("format", "")
            guidance = item.get("guidance", "")
            char_range = item.get("char_range", "")
            line = f"- **{section}**（{fmt}）: {guidance}"
            if char_range:
                line += f" [{char_range}]"
            lines.append(line)
        elif isinstance(item, str):
            lines.append(f"- {item}")
    return "\n".join(lines)


def build_messages(
    rec: Dict[str, Any],
    themes: List[str],
    prompts_cfg: Dict[str, Any],
    extracted_text: str,
    image_count: int = 0,
    profile: str = "default",
    cites_target: bool = False,
    profile_data: Dict[str, Any] | None = None,
) -> List[Dict[str, str]]:
    """Build the initial LLM prompt messages for article generation."""
    # 根据 profile 选择配置
    # 优先使用显式传入的 profile_data（来自 profile_{name}.yaml），
    # 其次查 prompts_cfg.profiles 内联配置，最后回退根级配置
    if profile_data is not None:
        profile_cfg = profile_data
    else:
        profile_cfg = prompts_cfg.get("profiles", {}).get(profile, {})
    # profile_cfg 为空 dict 时使用根级配置（向后兼容）
    active_domain = load_domain()
    effective_system_prompt = profile_cfg.get("system_prompt") or prompts_cfg.get("system_prompt", "你是科研编辑。")
    effective_system_prompt = render_domain_placeholders(effective_system_prompt, domain=active_domain)
    effective_structure = render_domain_placeholders(
        profile_cfg.get("article_structure") or prompts_cfg.get("article_structure", []),
        domain=active_domain,
    )
    theme_templates = render_domain_placeholders(
        profile_cfg.get("theme_templates") or prompts_cfg.get("theme_templates", {}),
        domain=active_domain,
    )

    from core.entity_detector import get_target_label

    target_label = get_target_label()
    if cites_target and target_label and rec.get("entity_label") == target_label:
        target_suffix = (
            f"\n\n# {target_label} 课题标注要求\n"
            f"论文资料确认提及了 {target_label}。如与研究内容相关，请在适当位置准确标注该课题，"
            "不要补充原文未提供的产品信息或技术参数。"
        )
        effective_system_prompt = effective_system_prompt + target_suffix

    # Theme blocks
    theme_section = ""
    if themes:
        theme_lines = []
        for t in themes:
            template = theme_templates.get(t, f"围绕「{t}」主题进行深入解读，分析其在该方向的意义、应用前景和局限。")
            theme_lines.append(f"### 主题「{t}」的解读要求：\n{template.strip()}")
        theme_section = "\n\n".join(theme_lines)
    else:
        theme_section = "无指定主题，请做全面平衡的解读。"

    # Structure guidance
    structure_guidance = _build_structure_guidance({"article_structure": effective_structure})

    device_context = _format_device_context_block(rec, target_label=target_label)
    device_section = ""
    if device_context:
        device_section = (
            "\n\n# 设备补充信息（来自论文合集 Excel，优先可信）\n\n"
            f"{device_context}\n\n"
            "写作约束：当论文全文提取因长度限制被截断、导致方法细节不完整时，"
            "请优先使用以上设备信息补全方法亮点与参考信息；不得与正文已给出的事实冲突。"
        )

    # Image placement hint (disabled: no [插图N] markers in body)
    image_hint = ""

    content = f"""# 论文信息

{_format_metadata_block(rec)}

# 主题侧重

{theme_section}

# 写作要求

{structure_guidance}
{image_hint}
{device_section}

# 论文全文提取（可能不完整）

{extracted_text}
""".strip()

    return [
        {"role": "system", "content": effective_system_prompt},
        {"role": "user", "content": content},
    ]


def refine_messages(draft: str, prompts_cfg: Dict[str, Any] | None = None, profile_data: Dict[str, Any] | None = None) -> List[Dict[str, str]]:
    """Build the refinement/editing prompt messages."""
    # 优先使用 profile 的 refine_prompt，其次查根级配置
    effective_refine = None
    if profile_data:
        effective_refine = profile_data.get("refine_prompt")
    if not effective_refine and prompts_cfg:
        effective_refine = prompts_cfg.get("refine_prompt")
    if effective_refine:
        refine_prompt = render_domain_placeholders(effective_refine, domain=load_domain()).strip()
        return [
            {"role": "system", "content": "你是中文学术写作终审编辑，严格遵循编辑指令进行润色。"},
            {"role": "user", "content": f"{refine_prompt}\n\n{draft}"},
        ]

    # Fallback if no refine_prompt in config
    return [
        {
            "role": "system",
            "content": "你是中文学术写作终审编辑。保持事实准确，增强可读性与传播性。",
        },
        {
            "role": "user",
            "content": (
                "请对以下推文草稿进行终审润色。"
                "要求：1)检查数据引用一致性 2)删除冗余表述 3)强化段落逻辑衔接 4)确保每段有核心金句。"
                "严禁添加润色说明或分隔线。直接输出润色后的完整文章。\n\n"
                f"{draft}"
            ),
        },
    ]
