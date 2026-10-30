"""Stable, deterministic pipeline for paper-summary Word reports."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from fastapi import HTTPException, Request
from sqlalchemy import select

from core.config_loader import ConfigLoader
from core.entity_detector import get_target_label
from core.llm_client import OpenAICompatibleClient
from core.word_report_generator import generate_word_report, translate_brain_region
from paper_library.citation import build_apa_reference, extract_crossref_author_names
from paper_library.db import get_session
from paper_library.models import Author, JournalMetric, Paper, paper_authors
from paper_library.time_utils import local_now

from .config import settings
from .governance_integration import GovernedPaperOutput
from .temp_download import build_temp_download_url, create_temp_file

ProgressCallback = Callable[[int, str], None]
logger = logging.getLogger(__name__)

NARRATIVE_KEYS = (
    "technical_scope",
    "population_topics",
    "experimental_design",
    "method_analysis",
    "core_findings",
    "conclusion",
)

META_PHRASES = ("数据不足", "证据不足", "未提供足够证据", "无法判断", "基于所给材料", "自动生成")
REFERENCE_TOKEN_RE = re.compile(r"〔(REF_\d{3})〕")
ANY_REFERENCE_TOKEN_RE = re.compile(r"〔(REF_[^〕]+)〕")
NUMERIC_CITATION_SEQUENCE_RE = re.compile(r"\[\d+\](?:\s*[、,，]?\s*\[\d+\])+")

NARRATIVE_SCHEMA_EXAMPLE = {
    "report_title": "中文主标题",
    "report_subtitle": "中文副标题",
    "overview": "150-350字概述",
    "narrative": {
        "technical_scope": {"content": "技术背景与研究范围"},
        "population_topics": {
            "content": "人群与主题综合分析",
            "subsections": [{"heading": "子主题", "content": "跨论文比较"}],
        },
        "experimental_design": {"content": "任务范式与实验设计综述"},
        "method_analysis": {
            "heading": "参数图与定量指标",
            "content": "方法学分析",
        },
        "core_findings": {
            "content": "核心发现总述",
            "subsections": [
                {"heading": "发现一", "content": "跨论文归纳"},
                {"heading": "发现二", "content": "差异与联系"},
            ],
        },
        "conclusion": {
            "content": "总结段落",
            "bullets": ["要点一", "要点二", "要点三", "要点四", "要点五"],
        },
    },
}


def _progress(callback: ProgressCallback | None, value: int, stage: str) -> None:
    if callback:
        callback(value, stage)


def _parse_json_field(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    parsed = _parse_json_field(value)
    if not isinstance(parsed, list):
        return []
    result: list[str] = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("name") or item.get("label") or item.get("value")
            if label:
                result.append(str(label).strip())
    return list(dict.fromkeys(result))


def collect_summary_papers(paper_ids: Iterable[int]) -> list[dict[str, Any]]:
    """Collect bounded evidence cards. Database facts remain authoritative."""
    ids = list(dict.fromkeys(int(item) for item in paper_ids))
    summary_limit = 1000 if len(ids) <= 10 else 600 if len(ids) <= 30 else 350
    papers: list[dict[str, Any]] = []
    target_label = get_target_label()
    with get_session(workspace=settings.workspace, config_dir=settings.config_dir) as session:
        for paper_id in ids:
            paper = session.get(Paper, paper_id)
            if not paper:
                continue
            metric = None
            if paper.journal_id:
                metric = (
                    session.query(JournalMetric)
                    .filter(JournalMetric.journal_id == paper.journal_id)
                    .order_by(JournalMetric.year.desc())
                    .first()
                )
            summary = (
                paper.abstract_zh
                or paper.abstract
                or paper.conclusions_zh
                or paper.extracted_text
                or ""
            )[:summary_limit]
            brain_regions = [translate_brain_region(item) for item in _string_list(paper.brain_regions)]
            author_names = extract_crossref_author_names(paper.doi_meta)
            if not author_names:
                author_names = list(session.execute(
                    select(Author.name)
                    .select_from(paper_authors)
                    .join(Author, paper_authors.c.author_id == Author.id)
                    .where(paper_authors.c.paper_id == paper.id)
                    .order_by(paper_authors.c.author_order)
                ).scalars())
            reference_key = f"REF_{len(papers) + 1:03d}"
            papers.append({
                "id": paper.id,
                "internal_id": paper.internal_id,
                "entity_label": (
                    paper.entity_label or ""
                    if paper.cites_target and paper.entity_label == target_label
                    else ""
                ),
                "cites_target": paper.cites_target,
                "reference_key": reference_key,
                "title": paper.title or "",
                "title_zh": paper.title_zh or "",
                "journal_name": paper.journal_name or "",
                "year": paper.year,
                "volume": paper.volume or "",
                "issue": paper.issue or "",
                "pages": paper.pages or "",
                "doi": paper.doi or "",
                "impact_factor": metric.impact_factor if metric else None,
                "jcr_quartile": metric.jcr_quartile if metric else None,
                "summary": summary,
                "conclusions_zh": (paper.conclusions_zh or "")[:500],
                "highlights_zh": _string_list(paper.highlights_zh)[:8],
                "population": _string_list(paper.population)[:8],
                "paradigm": _string_list(paper.paradigms or paper.paradigm)[:8],
                "brain_regions": brain_regions[:10],
                "keywords_zh": _string_list(paper.keywords_zh)[:10],
                "research_fields": _string_list(paper.research_topics or paper.research_fields)[:8],
                "apa_reference": build_apa_reference(
                    author_names=author_names,
                    year=paper.year,
                    title=paper.title,
                    journal=paper.journal_name or (paper.journal.name if paper.journal else ""),
                    volume=paper.volume,
                    issue=paper.issue,
                    pages=paper.pages,
                    doi=paper.doi,
                ),
            })
    return papers


def _paper_ref(paper: Mapping[str, Any]) -> str:
    return f"〔{paper.get('reference_key')}〕"


def _join(values: Iterable[Any], default: str = "—") -> str:
    items = [str(item).strip() for item in values if str(item).strip()]
    return "、".join(dict.fromkeys(items)) if items else default


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def build_reference_number_map(
    payload: Mapping[str, Any],
    papers: list[dict[str, Any]],
) -> dict[str, int]:
    """Number sources by first narrative appearance, then selected order."""
    valid_keys = {str(paper["reference_key"]) for paper in papers}
    ordered_keys: list[str] = []
    narrative = payload.get("narrative")
    ordered_content: list[Any] = [payload.get("overview")]
    if isinstance(narrative, Mapping):
        ordered_content.extend(narrative.get(key) for key in NARRATIVE_KEYS)
    for text in _walk_strings(ordered_content):
        for key in REFERENCE_TOKEN_RE.findall(text):
            if key in valid_keys and key not in ordered_keys:
                ordered_keys.append(key)
    for paper in papers:
        key = str(paper["reference_key"])
        if key not in ordered_keys:
            ordered_keys.append(key)
    return {key: index + 1 for index, key in enumerate(ordered_keys)}


def replace_reference_tokens(value: Any, numbers: Mapping[str, int]) -> Any:
    if isinstance(value, str):
        resolved = REFERENCE_TOKEN_RE.sub(
            lambda match: f"[{numbers[match.group(1)]}]" if match.group(1) in numbers else "",
            value,
        )
        return NUMERIC_CITATION_SEQUENCE_RE.sub(
            lambda match: "[" + ", ".join(dict.fromkeys(re.findall(r"\[(\d+)\]", match.group(0)))) + "]",
            resolved,
        )
    if isinstance(value, list):
        return [replace_reference_tokens(item, numbers) for item in value]
    if isinstance(value, dict):
        return {key: replace_reference_tokens(item, numbers) for key, item in value.items()}
    return value


def build_reference_entries(
    papers: list[dict[str, Any]],
    numbers: Mapping[str, int],
) -> list[dict[str, Any]]:
    entries = []
    for paper in papers:
        reference = dict(paper.get("apa_reference") or {})
        reference["number"] = numbers[str(paper["reference_key"])]
        reference["paper_id"] = paper["id"]
        entries.append(reference)
    return sorted(entries, key=lambda item: int(item["number"]))


def ensure_reference_metadata(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep report assembly deterministic for tests and persisted legacy data."""
    result: list[dict[str, Any]] = []
    for index, source in enumerate(papers, start=1):
        paper = dict(source)
        paper.setdefault("reference_key", f"REF_{index:03d}")
        if not paper.get("apa_reference"):
            paper["apa_reference"] = build_apa_reference(
                author_names=paper.get("author_names") or [],
                year=paper.get("year"),
                title=paper.get("title"),
                journal=paper.get("journal_name"),
                volume=paper.get("volume"),
                issue=paper.get("issue"),
                pages=paper.get("pages"),
                doi=paper.get("doi"),
            )
        result.append(paper)
    return result


def _section(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    narrative = payload.get("narrative")
    value = narrative.get(key) if isinstance(narrative, dict) else None
    return value if isinstance(value, dict) else {}


def _subsections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "").strip()
        content = str(item.get("content") or "").strip()
        if heading and content:
            result.append({"heading": heading, "level": 2, "content": content})
    return result


def validate_narrative_payload(
    payload: Any,
    reference_keys: Iterable[str] | None = None,
) -> list[str]:
    """Return stable error codes suitable for targeted same-model repair."""
    if not isinstance(payload, dict):
        return ["TOP_LEVEL_NOT_OBJECT"]
    errors: list[str] = []
    if len(str(payload.get("report_title") or "").strip()) < 4:
        errors.append("REPORT_TITLE_MISSING")
    if len(str(payload.get("overview") or "").strip()) < 80:
        errors.append("OVERVIEW_TOO_SHORT")
    narrative = payload.get("narrative")
    if not isinstance(narrative, dict):
        return errors + ["NARRATIVE_MISSING"]
    for key in NARRATIVE_KEYS:
        item = narrative.get(key)
        if not isinstance(item, dict):
            errors.append(f"SECTION_CONTENT_MISSING:{key}")
            continue
        content = str(item.get("content") or "").strip()
        subsection_content = "".join(sub["content"] for sub in _subsections(item.get("subsections")))
        minimum = 100 if key in {"population_topics", "core_findings"} else 40
        if len(content + subsection_content) < minimum:
            errors.append(f"SECTION_CONTENT_MISSING:{key}")
        if reference_keys is not None and key != "conclusion":
            citations = set(REFERENCE_TOKEN_RE.findall(content + subsection_content))
            if not citations:
                errors.append(f"CITATION_MISSING:{key}")
    if len(_subsections(_section(payload, "population_topics").get("subsections"))) < 2:
        errors.append("SUBSECTIONS_TOO_FEW:population_topics")
    if len(_subsections(_section(payload, "core_findings").get("subsections"))) < 2:
        errors.append("SUBSECTIONS_TOO_FEW:core_findings")
    bullets = _section(payload, "conclusion").get("bullets")
    if not isinstance(bullets, list) or len([item for item in bullets if str(item).strip()]) < 5:
        errors.append("BULLETS_TOO_FEW:conclusion")
    serialized = json.dumps(payload, ensure_ascii=False)
    for phrase in META_PHRASES:
        if phrase in serialized:
            errors.append(f"META_PHRASE:{phrase}")
    if reference_keys is not None:
        valid_keys = set(reference_keys)
        for key in ANY_REFERENCE_TOKEN_RE.findall(serialized):
            if key not in valid_keys:
                errors.append(f"UNKNOWN_REFERENCE:{key}")
        if re.search(r"#\d{1,8}", serialized):
            errors.append("INTERNAL_ID_REFERENCE_FOUND")
    return errors


def _active_domain_config() -> dict[str, Any]:
    loader = ConfigLoader(Path(settings.workspace) / settings.config_dir)
    return loader._load_domain()


def _fallback_content(
    key: str,
    papers: list[dict[str, Any]],
    domain_config: Mapping[str, Any] | None = None,
) -> str:
    domain = dict(domain_config or _active_domain_config())
    domain_name = str(domain.get("name") or domain.get("short_name") or "研究领域")
    domain_short_name = str(domain.get("short_name") or domain_name)
    refs = "、".join(_paper_ref(paper) for paper in papers[:8])
    populations = _join(item for paper in papers for item in paper.get("population", []))
    paradigms = _join(item for paper in papers for item in paper.get("paradigm", []))
    regions = _join(item for paper in papers for item in paper.get("brain_regions", []))
    templates = {
        "technical_scope": f"这些研究围绕{domain_name}相关问题展开，从不同研究对象、方法和任务切入。{refs} 共同构成了从实验设计到结果解释的研究脉络，各项工作的侧重点相互补充。",
        "population_topics": f"研究对象涵盖{populations}。不同群体的任务配合、功能状态和研究目标并不相同，使各项研究在问题设置与结果解释上形成了层次分明的比较关系，相关来源见{refs}。",
        "experimental_design": f"研究采用的任务与观察方式主要包括{paradigms}。这些设计分别强调诱发反应、状态差异或功能联系，为跨研究理解结果的一致性与差异提供了方法基础{refs}。",
        "method_analysis": f"相关工作涉及的脑区或组织观察范围包括{regions}。研究在测量指标、空间覆盖、任务安排和分析视角上的差异，为理解结果提供了不同参照{refs}。",
        "core_findings": f"综合 {refs}，各项研究从不同研究对象和实验条件呈现了定量测量结果与影像特征。共同趋势与研究间差异并存，结果应结合具体方法和样本特征解释。",
        "conclusion": f"这些研究展示了{domain_short_name}在不同研究问题和应用场景中的研究进展。将研究对象、方法和观察结果放在同一框架中比较，有助于识别共同趋势，也能保留各项工作的情境差异。",
    }
    return templates[key]


def _replace_meta_phrases(value: Any) -> Any:
    replacements = {
        "数据不足": "相关研究仍在积累",
        "证据不足": "研究结论尚未完全一致",
        "未提供足够证据": "相关结论仍需更多研究相互印证",
        "无法判断": "仍需结合更多研究比较",
        "基于所给材料": "从这些研究来看",
        "自动生成": "综合整理",
    }
    if isinstance(value, str):
        for source, target in replacements.items():
            value = value.replace(source, target)
        return value
    if isinstance(value, list):
        return [_replace_meta_phrases(item) for item in value]
    if isinstance(value, dict):
        return {key: _replace_meta_phrases(item) for key, item in value.items()}
    return value


def complete_narrative_payload(
    payload: Mapping[str, Any],
    papers: list[dict[str, Any]],
    domain_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically fill only structurally missing prose after model repairs."""
    domain = dict(domain_config or _active_domain_config())
    domain_name = str(domain.get("name") or domain.get("short_name") or "研究领域")
    domain_short_name = str(domain.get("short_name") or domain_name)
    result = _replace_meta_phrases(json.loads(json.dumps(payload, ensure_ascii=False)))
    result.setdefault("report_title", f"{domain_short_name}研究分析报告")
    result.setdefault("report_subtitle", f"基于 {len(papers)} 篇论文的综合分析")
    if len(str(result.get("overview") or "")) < 80:
        result["overview"] = (
            f"本报告汇集 {len(papers)} 篇{domain_name}相关研究，从研究对象、方法、观察结果和核心发现等层面进行综合比较。"
            "各项工作采用不同设计回答相互关联的问题，既呈现共同的技术路径，也保留了群体特征和实验条件带来的差异。"
        )
    narrative = result.setdefault("narrative", {})
    if not isinstance(narrative, dict):
        narrative = {}
        result["narrative"] = narrative
    for key in NARRATIVE_KEYS:
        item = narrative.get(key)
        if not isinstance(item, dict):
            item = {}
            narrative[key] = item
        content = str(item.get("content") or "").strip()
        subsection_content = "".join(sub["content"] for sub in _subsections(item.get("subsections")))
        minimum = 100 if key in {"population_topics", "core_findings"} else 40
        if len(content + subsection_content) < minimum:
            item["content"] = _fallback_content(key, papers, domain)
        elif key != "conclusion" and not REFERENCE_TOKEN_RE.search(content + subsection_content):
            refs = "".join(_paper_ref(paper) for paper in papers[: min(5, len(papers))])
            item["content"] = f"{content} 相关研究见{refs}。"
    for key, headings in {
        "population_topics": ("研究对象的差异", "主题组织与比较"),
        "core_findings": ("共同观察", "研究间差异"),
    }.items():
        item = narrative[key]
        existing = _subsections(item.get("subsections"))
        if len(existing) < 2:
            fallback = _fallback_content(key, papers, domain)
            item["subsections"] = [
                {"heading": heading, "content": fallback} for heading in headings
            ]
    conclusion = narrative["conclusion"]
    bullets = [str(item).strip() for item in conclusion.get("bullets", []) if str(item).strip()]
    if len(bullets) < 5:
        conclusion["bullets"] = [
            "研究对象和应用情境呈现多样化特征",
            "方法与测量指标共同决定结果的解释边界",
            "影像特征及其空间分布需要结合研究设计理解",
            "跨研究比较应保留群体和范式差异",
            "方法选择应与研究问题、样本特征和数据解释范围相匹配",
        ]
    return result


def _method_heading(
    papers: list[dict[str, Any]],
    proposed: Any,
    domain_config: Mapping[str, Any] | None = None,
) -> str:
    domain = dict(domain_config or _active_domain_config())
    strengths_title = str((domain.get("prompt") or {}).get("strengths_section_title") or "方法适用条件分析")
    text = str(proposed or "").strip().removeprefix("四、")
    allowed = (
        "参数图与定量指标",
        "实验设计与方法学特点",
        strengths_title,
    )
    if any(item in text for item in allowed):
        return "四、" + next(item for item in allowed if item in text)
    if any(paper.get("brain_regions") for paper in papers):
        return "四、参数图与定量指标"
    if any(paper.get("paradigm") for paper in papers):
        return "四、实验设计与方法学特点"
    return f"四、{strengths_title}"


def build_deterministic_report(
    payload: Mapping[str, Any],
    papers: list[dict[str, Any]],
    domain_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn model prose into a fixed seven-section renderer contract."""
    papers = ensure_reference_metadata(papers)
    domain = dict(domain_config or _active_domain_config())
    domain_short_name = str(domain.get("short_name") or domain.get("name") or "研究领域")
    reference_numbers = build_reference_number_map(payload, papers)
    resolved_payload = replace_reference_tokens(payload, reference_numbers)
    references = build_reference_entries(papers, reference_numbers)
    technical = _section(resolved_payload, "technical_scope")
    population = _section(resolved_payload, "population_topics")
    design = _section(resolved_payload, "experimental_design")
    method = _section(resolved_payload, "method_analysis")
    findings = _section(resolved_payload, "core_findings")
    conclusion = _section(resolved_payload, "conclusion")
    return {
        "report_title": str(resolved_payload.get("report_title") or f"{domain_short_name}研究分析报告").strip(),
        "report_subtitle": str(resolved_payload.get("report_subtitle") or f"基于 {len(papers)} 篇论文的综合分析").strip(),
        "overview": str(resolved_payload.get("overview") or "").strip(),
        "sections": [
            {"heading": "一、技术概述与研究范围", "level": 1, "content": str(technical.get("content") or "").strip()},
            {"heading": "二、研究人群与主题分布", "level": 1, "content": str(population.get("content") or "").strip(), "subsections": _subsections(population.get("subsections"))},
            {"heading": "三、任务范式与实验设计", "level": 1, "content": str(design.get("content") or "").strip()},
            {"heading": _method_heading(papers, method.get("heading"), domain), "level": 1, "content": str(method.get("content") or "").strip()},
            {"heading": "五、核心发现综述", "level": 1, "content": str(findings.get("content") or "").strip(), "subsections": _subsections(findings.get("subsections"))},
            {"heading": "六、总结", "level": 1, "content": str(conclusion.get("content") or "").strip(), "bullets": [str(item).strip() for item in conclusion.get("bullets", []) if str(item).strip()][:8]},
            {"heading": "参考文献", "level": 1, "references": references},
        ],
    }


def validate_final_report(report: Mapping[str, Any], paper_count: int) -> list[str]:
    errors: list[str] = []
    sections = report.get("sections")
    if not isinstance(sections, list) or len(sections) != 7:
        errors.append("FINAL_SECTION_COUNT_INVALID")
        return errors
    if len(str(report.get("overview") or "")) < 80:
        errors.append("FINAL_OVERVIEW_TOO_SHORT")
    tables = [item.get("table") for item in sections if isinstance(item, dict) and item.get("table")]
    if tables:
        errors.append("FINAL_TABLE_UNEXPECTED")
    reference_sections = [
        item for item in sections
        if isinstance(item, dict) and item.get("references") is not None
    ]
    if len(reference_sections) != 1:
        errors.append("FINAL_REFERENCE_SECTION_INVALID")
    else:
        references = reference_sections[0].get("references") or []
        numbers = [int(item.get("number", 0)) for item in references if isinstance(item, dict)]
        if numbers != list(range(1, paper_count + 1)):
            errors.append("FINAL_REFERENCE_NUMBERING_INVALID")
        if any(not str(item.get("text") or "").strip() for item in references if isinstance(item, dict)):
            errors.append("FINAL_REFERENCE_TEXT_MISSING")
    serialized = json.dumps(report, ensure_ascii=False)
    if REFERENCE_TOKEN_RE.search(serialized) or re.search(r"#\d{1,8}", serialized):
        errors.append("FINAL_INTERNAL_REFERENCE_FOUND")
    return errors


def _clean_json_content(content: str) -> str:
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*\n?", "", value)
        value = re.sub(r"\n?\s*```\s*$", "", value)
    return value


def _prompt(
    papers: list[dict[str, Any]],
    domain_config: Mapping[str, Any] | None = None,
) -> str:
    papers = ensure_reference_metadata(papers)
    domain = dict(domain_config or _active_domain_config())
    domain_name = str(domain.get("name") or domain.get("short_name") or "研究领域")
    domain_intro = str((domain.get("prompt") or {}).get("domain_intro") or "")
    target_label = get_target_label()
    target_guidance = ""
    if target_label and any(
        paper.get("cites_target") and paper.get("entity_label") == target_label
        for paper in papers
    ):
        target_guidance = (
            f"\n如证据卡确认提及 {target_label}，仅在相关分析处准确写出该名称；"
            "不得将其扩展成产品宣传或补写原文未提供的信息。\n"
        )
    hidden_keys = {"id", "internal_id", "apa_reference", "impact_factor", "jcr_quartile"}
    compact = []
    for paper in papers:
        card = {
            key: value for key, value in paper.items()
            if key not in hidden_keys and value not in (None, "", [], {})
        }
        card["citation_key"] = f"〔{paper['reference_key']}〕"
        card.pop("reference_key", None)
        compact.append(card)
    return f"""基于下面 {len(papers)} 篇{domain_name}相关论文撰写中文研究分析报告的叙述内容。

领域背景：{domain_intro}

论文证据卡：
{json.dumps(compact, ensure_ascii=False, separators=(",", ":"))}

只返回一个 JSON 对象，严格采用以下键名和层级：
{json.dumps(NARRATIVE_SCHEMA_EXAMPLE, ensure_ascii=False, indent=2)}

注意：文献编号和参考文献均由程序生成，你不要输出表格或参考文献。
narrative 中六个键必须全部存在，不得删除、合并或改名。除总结外，每个部分都要使用证据卡中的
citation_key 标明来源，必须原样使用“〔REF_001〕”格式，不得改写为数字编号、internal id、作者年份或 DOI。
同一句引用多篇论文时连续写出多个标记，例如“〔REF_001〕〔REF_003〕”。
可用信息较少时收束论断，围绕研究对象、方法差异或观察结果自然衔接，不得编造。
{target_guidance}
正文不得出现“数据不足”“证据不足”“未提供足够证据”“无法判断”“基于所给材料”“自动生成”等元表述。
不得提及设备型号、通道数、波长或采样率。正文总量控制在 2500-4000 字。
"""


def _repair_prompt(payload: Any, errors: list[str]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)
    return f"""上一版报告叙述 JSON 未通过校验。只修复列出的问题，保留已经合格的具体内容，
并返回完整 JSON 对象，不要解释或使用 Markdown。

校验错误：{json.dumps(errors, ensure_ascii=False)}
固定结构：{json.dumps(NARRATIVE_SCHEMA_EXAMPLE, ensure_ascii=False)}
上一版内容：{serialized[:24000]}

六个 narrative 键必须全部存在。不得出现“数据不足”“证据不足”“未提供足够证据”“无法判断”、
“基于所给材料”或“自动生成”等元表述，不得编造论文事实。引用只能保留上一轮证据卡中已有的
“〔REF_001〕”形式标记，不得输出 internal id、#编号、数字参考文献或自行编写参考文献。
"""


def resolve_summary_model(model: str | None) -> str:
    """Resolve auto selection to one concrete model; never add a fallback model."""
    loader = ConfigLoader(Path(settings.workspace) / settings.config_dir)
    models_config = loader.load_all().get("models", {})
    selected = None if model in (None, "", "auto") else model
    if not selected:
        sequence = models_config.get("routing", {}).get("default_auto_sequence", [])
        selected = sequence[0] if sequence else models_config.get("default_model")
    configured = {item["id"] for item in models_config.get("models", [])}
    if not selected or selected not in configured:
        raise HTTPException(status_code=500, detail="论文总结模型配置不存在")
    return selected


def generate_report_json(
    llm: OpenAICompatibleClient,
    model: str,
    model_config: Mapping[str, Any],
    system_prompt: str,
    papers: list[dict[str, Any]],
    *,
    max_output_tokens: int,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], int]:
    """Generate prose and perform up to two targeted same-model repairs."""
    papers = ensure_reference_metadata(papers)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _prompt(papers)},
    ]
    payload: Any = None
    errors: list[str] = []
    repair_count = 0
    for attempt in range(3):
        _progress(progress, 35 + attempt * 15, "正在生成报告内容" if attempt == 0 else "正在修复报告结构")
        content = llm.chat(
            model,
            messages,
            max_tokens=min(max_output_tokens, int(model_config.get("max_output_tokens", max_output_tokens))),
            thinking=model_config.get("thinking", False),
            response_format="json_object",
            timeout_override=model_config.get("timeout_seconds", 600),
        )
        try:
            payload = json.loads(_clean_json_content(content))
            errors = validate_narrative_payload(
                payload,
                (str(paper["reference_key"]) for paper in papers),
            )
        except json.JSONDecodeError as exc:
            payload = content
            errors = [f"INVALID_JSON:{exc.msg}"]
        if errors:
            logger.warning("Word summary validation attempt %d failed: %s", attempt + 1, errors)
        if not errors:
            report = build_deterministic_report(payload, papers)
            final_errors = validate_final_report(report, len(papers))
            if not final_errors:
                return report, repair_count
            errors = final_errors
        if attempt < 2:
            repair_count += 1
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _prompt(papers)},
                {"role": "user", "content": _repair_prompt(payload, errors)},
            ]
    if isinstance(payload, dict):
        completed = complete_narrative_payload(payload, papers)
        completion_errors = validate_narrative_payload(
            completed,
            (str(paper["reference_key"]) for paper in papers),
        )
        if not completion_errors:
            report = build_deterministic_report(completed, papers)
            if not validate_final_report(report, len(papers)):
                logger.warning("Word summary used deterministic completion after %d repair(s)", repair_count)
                return report, repair_count
    raise RuntimeError("报告结构修复失败: " + ", ".join(errors))


def generate_summary_artifact(
    paper_ids: Iterable[int],
    *,
    profile: str,
    model: str | None,
    actor: Mapping[str, Any],
    request: Request | None = None,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Run the resumable report pipeline and create a downloadable Word file."""
    ids = list(dict.fromkeys(int(item) for item in paper_ids))
    _progress(progress, 10, "正在整理论文数据")
    papers = collect_summary_papers(ids)
    if not papers:
        raise HTTPException(status_code=400, detail="有效论文不足 1 篇")

    loader = ConfigLoader(Path(settings.workspace) / settings.config_dir)
    all_config = loader.load_all()
    models_config = all_config.get("models", {})
    selected_model = resolve_summary_model(model)
    configured = {item["id"]: item for item in models_config.get("models", [])}
    model_config = configured.get(selected_model or "", {})
    if not selected_model or not model_config:
        raise HTTPException(status_code=500, detail="论文总结模型配置不存在")

    profile_data = loader.load_profile(profile or "report")
    runtime = all_config.get("runtime", {})
    provider = runtime.get("provider", {})
    llm = OpenAICompatibleClient(
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout_seconds=provider.get("timeout", 300),
    )
    quota = GovernedPaperOutput(actor, [paper["id"] for paper in papers], action="paper.generate_word_summary", request=request)
    try:
        report, repair_count = generate_report_json(
            llm,
            selected_model,
            model_config,
            profile_data.get("system_prompt", "你是科研写作专家。"),
            papers,
            max_output_tokens=int(profile_data.get("defaults_override", {}).get("max_output_tokens", 16000)),
            progress=progress,
        )
        _progress(progress, 80, "正在生成 Word 文件")
        word_bytes = generate_word_report(report)
        if len(word_bytes) < 5000:
            raise RuntimeError("Word 文件生成结果异常")
        filename = f"论文总结_{local_now().strftime('%Y%m%d')}.docx"
        temp_info = create_temp_file(
            content=word_bytes,
            filename=filename,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        quota.complete(temp_info, details={"format": "docx", "model": selected_model, "repair_count": repair_count})
        result = {
            "success": True,
            "download_url": build_temp_download_url(temp_info),
            "filename": temp_info["filename"],
            "model": selected_model,
            "file_id": temp_info["file_id"],
            "expires_at": temp_info["expires_at"],
        }
        _progress(progress, 100, "已完成")
        return result, report, repair_count
    except Exception as exc:
        quota.fail(exc)
        raise
