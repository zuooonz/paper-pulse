"""Deterministic abstract recovery and bounded Chinese translation.

Original abstracts are only copied from authoritative metadata or extracted
from an explicitly labelled full-text Abstract/Summary section.  The LLM is
used only to translate an existing original abstract; it never invents one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import requests

from core.crossref_api import clean_crossref_abstract, fetch_metadata_by_doi


_SPACED_ABSTRACT = re.compile(r"(?i)\bA\s+B\s+S\s+T\s+R\s+A\s+C\s+T\b")
_SPACED_SUMMARY = re.compile(r"(?i)\bS\s+U\s+M\s+M\s+A\s+R\s+Y\b")
_ABSTRACT_START = re.compile(
    r"(?im)(?:^|\n)\s*(?:\d+(?:\.\d+)*[.\s]*)?"
    r"(?:abstract|summary)\s*[:：—-]?\s*"
)
_ABSTRACT_END = re.compile(
    r"(?im)(?:\n|\s{2,})\s*(?:\d+(?:\.\d+)*[.\s]*)?"
    r"(?:keywords?|key\s*words|index\s*terms|introduction|background|"
    r"r[ée]sum[ée]|acknowledg(?:e)?ments?)\b"
)
_STRUCTURED_START = re.compile(
    r"(?im)(?:^|\n)\s*(?P<label>background|purpose|objective)\s*:\s*"
)
_STRUCTURED_END = re.compile(
    r"(?im)(?:^|\n)\s*(?:keywords?|key\s*words|index\s*terms|"
    r"plain\s+language\s+summary|\d+(?:\.\d+)*[.\s]+introduction|introduction)\b"
)
_INTRODUCTION_START = re.compile(
    r"(?im)(?:^|\n)\s*(?:[IVXLCDM]+|\d+(?:\.\d+)*)[.\s]+introduction\s*$"
)
_HAN = re.compile(r"[\u3400-\u9fff]")
from core.service_config import api_endpoint, configured_value, contact_user_agent, warn_missing

_NCBI_BASE_URL = api_endpoint("NCBI_BASE_URL", "ncbi_base_url")
_PUBMED_EFETCH = f"{_NCBI_BASE_URL}/efetch.fcgi" if _NCBI_BASE_URL else ""


@dataclass(frozen=True)
class AbstractEnrichmentResult:
    changed: bool
    source: str
    error: str | None = None


def is_missing(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def contains_substantial_chinese(value: str | None) -> bool:
    text = re.sub(r"\s+", "", value or "")
    if not text:
        return False
    return len(_HAN.findall(text)) >= max(8, len(text) // 10)


def _output_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def needs_chinese_abstract_package(paper: Any) -> bool:
    """Whether translation, conclusion, or highlights still need generation."""
    return any(
        _output_missing(getattr(paper, field, None))
        for field in ("abstract_zh", "conclusions_zh", "highlights_zh")
    )


def _valid_chinese_summary(value: Any, minimum: int = 20) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if len(cleaned) >= minimum and contains_substantial_chinese(cleaned) else None


def _valid_chinese_highlights(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    valid = [item for item in cleaned if len(item) >= 6 and contains_substantial_chinese(item)]
    return valid[:5] if len(valid) >= 2 else None


def _apply_chinese_package(paper: Any, data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Fill only missing fields and return (changed, still-invalid fields)."""
    changed = False
    invalid: list[str] = []
    if _output_missing(getattr(paper, "abstract_zh", None)):
        value = _valid_chinese_summary(data.get("abstract_zh"))
        if value:
            paper.abstract_zh = value[:10000]
            changed = True
        else:
            invalid.append("abstract_zh")
    if _output_missing(getattr(paper, "conclusions_zh", None)):
        value = _valid_chinese_summary(data.get("conclusions_zh"))
        if value:
            paper.conclusions_zh = value[:3000]
            changed = True
        else:
            invalid.append("conclusions_zh")
    if _output_missing(getattr(paper, "highlights_zh", None)):
        value = _valid_chinese_highlights(data.get("highlights_zh"))
        if value:
            paper.highlights_zh = value
            changed = True
        else:
            invalid.append("highlights_zh")
    return changed, invalid


def extract_crossref_abstract(payload: Any) -> str | None:
    """Read an abstract from structured, raw, or message-wrapped Crossref data."""
    if not isinstance(payload, dict):
        return None
    candidates = [payload.get("abstract")]
    raw = payload.get("raw")
    if isinstance(raw, dict):
        candidates.append(raw.get("abstract"))
    message = payload.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("abstract"))
    for value in candidates:
        cleaned = clean_crossref_abstract(value)
        if len(cleaned) >= 30:
            return cleaned[:10000]
    return None


def fetch_pubmed_abstract(pmid: Any, timeout_sec: int = 15) -> str | None:
    """Fetch and clean a PubMed abstract without inventing missing content."""
    raw_pmid = str(pmid or "").strip()
    if not raw_pmid:
        return None
    try:
        normalized = str(int(float(raw_pmid)))
    except (TypeError, ValueError, OverflowError):
        normalized = raw_pmid
    params = {
        "db": "pubmed",
        "id": normalized,
        "retmode": "xml",
        "rettype": "abstract",
    }
    api_key = configured_value("NCBI_API_KEY", "ncbi", "api_key")
    email = configured_value("NCBI_EMAIL", "ncbi", "email")
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
    if not _PUBMED_EFETCH:
        warn_missing("NCBI", "NCBI_BASE_URL")
        return None
    try:
        response = requests.get(_PUBMED_EFETCH, params=params, timeout=timeout_sec, headers={"User-Agent": contact_user_agent()})
        response.raise_for_status()
    except requests.RequestException:
        return None
    xml = response.text
    matches = re.findall(
        r'<AbstractText([^>]*)>(.*?)</AbstractText>', xml, re.DOTALL | re.IGNORECASE,
    )
    parts: list[str] = []
    for attributes, content in matches:
        label_match = re.search(r'\bLabel="([^"]+)"', attributes, re.IGNORECASE)
        cleaned = clean_crossref_abstract(content)
        if not cleaned:
            continue
        label = clean_crossref_abstract(label_match.group(1)) if label_match else ""
        parts.append(f"{label}: {cleaned}" if label else cleaned)
    result = "\n".join(parts).strip()
    return result[:10000] if len(result) >= 30 else None


def extract_abstract_from_fulltext(value: str | None) -> str | None:
    """Extract only an explicitly labelled Abstract/Summary section."""
    if not value or len(value) < 200:
        return None
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    # A small class of older PDFs uses a font whose extracted character codes
    # are shifted by 29 (``$EVWUDFW`` decodes to ``Abstract``). Decode only
    # from that unambiguous marker onward; ordinary document text is untouched.
    shifted_marker = text.find("$EVWUDFW")
    if shifted_marker >= 0:
        suffix = text[shifted_marker:]
        text = text[:shifted_marker] + "".join(
            char if char.isspace() or ord(char) > 93 else chr(ord(char) + 29)
            for char in suffix
        )
    text = _SPACED_ABSTRACT.sub("ABSTRACT", text)
    text = _SPACED_SUMMARY.sub("SUMMARY", text)
    # Abstracts normally occur near the beginning. A bounded search avoids
    # matching references or an abstract quoted deep inside the article.
    head = text[:14000]
    for match in list(_ABSTRACT_START.finditer(head))[:3]:
        tail = head[match.end():]
        stop = _ABSTRACT_END.search(tail)
        candidate = tail[: stop.start() if stop else 5000]
        # Theses and some OCR outputs include an ``Abstract ........ iv``
        # table-of-contents entry before the real section.  Skip dot leaders
        # and continue to the next explicit heading instead of storing the TOC.
        if re.search(r"\.{8,}", candidate):
            continue
        candidate = re.sub(r"([A-Za-z])-\s*\n\s*([A-Za-z])", r"\1\2", candidate)
        candidate = re.sub(r"(?im)^\s*[ivxlcdm]{1,8}\s*$", "", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if 100 <= len(candidate) <= 5000:
            return candidate

    # Some publishers omit the Abstract heading and start directly with a
    # structured ``Background:`` or ``Purpose:`` block. Require later method
    # and result/conclusion labels so a body section is not mistaken for it.
    head = text[:10000]
    for match in list(_STRUCTURED_START.finditer(head))[:3]:
        tail = head[match.end():]
        stop = _STRUCTURED_END.search(tail)
        if not stop or stop.start() > 6000:
            continue
        candidate = f"{match.group('label').title()}: {tail[:stop.start()]}"
        labels = set(
            item.lower()
            for item in re.findall(
                r"(?im)(?:^|\n)\s*(methods?|results?|conclusions?)\s*:",
                candidate,
            )
        )
        if not any(item.startswith("method") for item in labels):
            continue
        if not any(item.startswith(("result", "conclusion")) for item in labels):
            continue
        candidate = re.sub(r"([A-Za-z])-\s*\n\s*([A-Za-z])", r"\1\2", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if 100 <= len(candidate) <= 6000:
            return candidate

    # AIAA-style conference papers may put an unlabelled summary between the
    # final affiliation line and a Roman-numeral Introduction heading.
    intro = _INTRODUCTION_START.search(head)
    if intro:
        preface = head[:intro.start()]
        affiliation_lines = list(
            re.finditer(
                r"(?im)^.*\b(?:department|university|institute|laboratory|centre|center)\b.*$",
                preface,
            )
        )
        if affiliation_lines:
            candidate = preface[affiliation_lines[-1].end():].strip()
            candidate = re.sub(r"([A-Za-z])-\s*\n\s*([A-Za-z])", r"\1\2", candidate)
            candidate = re.sub(r"\s+", " ", candidate).strip()
            research_signal = re.search(
                r"(?i)\b(?:this (?:study|research|work)|results? show|aims? to)\b",
                candidate,
            )
            if research_signal and 300 <= len(candidate) <= 5000:
                return candidate
    return None


def enrich_missing_abstract(
    paper: Any,
    *,
    refresh_crossref: bool = True,
    fetcher: Callable[..., dict[str, Any]] = fetch_metadata_by_doi,
    timeout_sec: int = 12,
    pubmed_fetcher: Callable[..., str | None] = fetch_pubmed_abstract,
    fetch_pubmed: bool = True,
    extract_fulltext: bool = True,
) -> AbstractEnrichmentResult:
    """Fill ``paper.abstract`` without overwriting an existing value."""
    if not is_missing(getattr(paper, "abstract", None)):
        return AbstractEnrichmentResult(False, "existing")

    stored = extract_crossref_abstract(getattr(paper, "doi_meta", None))
    if stored:
        paper.abstract = stored
        return AbstractEnrichmentResult(True, "doi_meta")

    crossref_error = None
    doi = (getattr(paper, "doi", None) or "").strip()
    if refresh_crossref and doi:
        try:
            result = fetcher(doi, structured=True, timeout_sec=timeout_sec)
            if result and not result.get("_error") and not result.get("error"):
                raw = result.get("raw")
                if isinstance(raw, dict) and raw:
                    paper.doi_meta = raw
                fetched = extract_crossref_abstract(result)
                if fetched:
                    paper.abstract = fetched
                    return AbstractEnrichmentResult(True, "crossref")
            elif result:
                crossref_error = str(result.get("_error") or result.get("error"))
        except Exception as exc:  # best effort; full text remains available
            crossref_error = str(exc)

    pmid = getattr(paper, "pmid", None)
    if fetch_pubmed and not is_missing(pmid):
        try:
            pubmed = pubmed_fetcher(pmid, timeout_sec=max(timeout_sec, 15))
            if pubmed:
                paper.abstract = pubmed
                return AbstractEnrichmentResult(True, "pubmed", crossref_error)
        except Exception as exc:
            if not crossref_error:
                crossref_error = f"PubMed: {exc}"

    if extract_fulltext:
        extracted = extract_abstract_from_fulltext(getattr(paper, "extracted_text", None))
        if extracted:
            paper.abstract = extracted
            return AbstractEnrichmentResult(True, "fulltext", crossref_error)

    return AbstractEnrichmentResult(False, "missing", crossref_error)


def _parse_json_object(value: str) -> dict[str, Any] | None:
    value = (value or "").strip()
    candidates = [value]
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    block = re.search(r"\{.*\}", value, re.DOTALL)
    if block:
        candidates.append(block.group())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def translate_abstract_to_chinese(
    paper: Any,
    client: Any,
    *,
    model: str = "example-chat-model",
) -> AbstractEnrichmentResult:
    """Generate missing Chinese translation, conclusion, and highlights once."""
    if not needs_chinese_abstract_package(paper):
        return AbstractEnrichmentResult(False, "existing_chinese_package")
    original = clean_crossref_abstract(getattr(paper, "abstract", None))
    if len(original) < 30:
        return AbstractEnrichmentResult(False, "no_original")
    copied_original = False
    if _output_missing(getattr(paper, "abstract_zh", None)) and contains_substantial_chinese(original):
        paper.abstract_zh = original
        copied_original = True
        if not needs_chinese_abstract_package(paper):
            return AbstractEnrichmentResult(True, "original_is_chinese")

    missing_fields = [
        field for field in ("abstract_zh", "conclusions_zh", "highlights_zh")
        if _output_missing(getattr(paper, field, None))
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "你是医学与神经科学论文翻译与编辑助手。只能依据给定摘要，"
                "不得增删研究结果、数值、因果关系或设备参数。保留专业缩写。只输出合法 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "一次完成缺失字段：abstract_zh 为完整忠实翻译，不得概括；"
                "conclusions_zh 为2-3句核心结论；highlights_zh 为3条简洁亮点。"
                f"只需生成这些字段：{json.dumps(missing_fields, ensure_ascii=False)}。"
                "返回格式：{\"abstract_zh\":\"...\",\"conclusions_zh\":\"...\","
                "\"highlights_zh\":[\"...\",\"...\",\"...\"]}\n\n"
                f"原文摘要：\n{original[:10000]}"
            ),
        },
    ]
    try:
        response = client.chat(
            model,
            messages,
            temperature=0.1,
            max_tokens=6000,
            timeout_override=300,
            thinking=False,
            response_format="json_object",
        )
        parsed = _parse_json_object(response)
        changed, invalid = _apply_chinese_package(paper, parsed or {})
        changed = changed or copied_original
        if invalid:
            return AbstractEnrichmentResult(
                changed, "package_partial" if changed else "translation_invalid",
                f"LLM 缺少有效字段: {', '.join(invalid)}",
            )
        return AbstractEnrichmentResult(changed, "llm_chinese_package")
    except Exception as exc:
        return AbstractEnrichmentResult(copied_original, "translation_error", str(exc))


def translate_abstract_batch_to_chinese(
    papers: list[Any],
    client: Any,
    *,
    model: str = "example-chat-model",
) -> dict[int, AbstractEnrichmentResult]:
    """Generate missing Chinese abstract packages for several papers per request."""
    results: dict[int, AbstractEnrichmentResult] = {}
    payload: list[dict[str, Any]] = []
    candidates: dict[int, Any] = {}
    prechanged: set[int] = set()
    for paper in papers:
        paper_id = int(getattr(paper, "id"))
        if not needs_chinese_abstract_package(paper):
            results[paper_id] = AbstractEnrichmentResult(False, "existing_chinese_package")
            continue
        original = clean_crossref_abstract(getattr(paper, "abstract", None))
        if len(original) < 30:
            results[paper_id] = AbstractEnrichmentResult(False, "no_original")
            continue
        if _output_missing(getattr(paper, "abstract_zh", None)) and contains_substantial_chinese(original):
            paper.abstract_zh = original
            prechanged.add(paper_id)
            if not needs_chinese_abstract_package(paper):
                results[paper_id] = AbstractEnrichmentResult(True, "original_is_chinese")
                continue
        candidates[paper_id] = paper
        payload.append({
            "id": paper_id,
            "abstract": original[:10000],
            "missing_fields": [
                field for field in ("abstract_zh", "conclusions_zh", "highlights_zh")
                if _output_missing(getattr(paper, field, None))
            ],
        })

    if not payload:
        return results

    messages = [
        {
            "role": "system",
            "content": (
                "你是医学与神经科学论文翻译与编辑助手。逐篇严格依据摘要，"
                "不得增删研究结果、数值、因果关系或设备参数，保留专业缩写。只输出合法 JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "为每篇论文一次生成 missing_fields：abstract_zh 是完整忠实翻译；"
                "conclusions_zh 是2-3句核心结论；highlights_zh 是3条简洁亮点。"
                "严格返回：{\"papers\":[{\"id\":1,\"abstract_zh\":\"...\","
                "\"conclusions_zh\":\"...\",\"highlights_zh\":[\"...\",\"...\",\"...\"]}]}\n\n"
                f"论文：{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        response = client.chat(
            model,
            messages,
            temperature=0.1,
            max_tokens=12000,
            timeout_override=300,
            thinking=False,
            response_format="json_object",
        )
        parsed = _parse_json_object(response)
        raw_items = parsed.get("papers") if parsed else None
        seen: set[int] = set()
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                try:
                    paper_id = int(item.get("id"))
                except (TypeError, ValueError):
                    continue
                if paper_id not in candidates:
                    continue
                changed, invalid = _apply_chinese_package(candidates[paper_id], item)
                changed = changed or paper_id in prechanged
                results[paper_id] = AbstractEnrichmentResult(
                    changed,
                    "package_partial" if invalid and changed else (
                        "translation_invalid" if invalid else "llm_chinese_package"
                    ),
                    f"LLM 缺少有效字段: {', '.join(invalid)}" if invalid else None,
                )
                seen.add(paper_id)
        for paper_id in candidates.keys() - seen:
            results[paper_id] = AbstractEnrichmentResult(
                paper_id in prechanged,
                "package_partial" if paper_id in prechanged else "translation_invalid",
                "批量生成缺少有效返回",
            )
    except Exception as exc:
        for paper_id in candidates:
            results[paper_id] = AbstractEnrichmentResult(
                paper_id in prechanged, "translation_error", str(exc)
            )
    return results
