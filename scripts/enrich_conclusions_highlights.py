#!/usr/bin/env python3
"""Fill missing abstracts, Chinese conclusions and highlights with bounded evidence.

The script never sends a complete paper to the LLM.  It first extracts an
explicit Abstract section deterministically, then uses either that abstract or
small Results/Discussion/Conclusion excerpts as evidence.  Existing values are
never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.config_loader import ConfigLoader  # noqa: E402
from core.llm_client import OpenAICompatibleClient  # noqa: E402
from core.entity_detector import get_target_label  # noqa: E402
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session  # noqa: E402
from paper_library.models import Paper  # noqa: E402


SYSTEM_PROMPT = """你是医学与神经科学论文信息整理助手。只依据给定证据，不补写证据中没有的结果、数值或因果关系。只输出合法 JSON。"""

USER_PROMPT = """请补全下列论文的缺失字段。每篇论文都给出了标题、缺失字段和受控证据。

字段要求：
- abstract_zh：若证据类型为“原文摘要”，忠实翻译为 2-4 句中文；否则生成 2-4 句“中文内容概述”，不得冒充原文摘要。
- conclusions_zh：用 1-2 句概括证据明确支持的核心结论，40-180 个中文字符。
- highlights_zh：恰好 3 条，每条 15-80 个中文字符，分别覆盖研究对象/方法、主要发现、意义或边界。
- 信息不足的字段返回 null。禁止添加设备参数、样本量、显著性、疗效或机制推断，除非证据明确提供。

返回格式：
{{"papers":[{{"id":1,"abstract_zh":"...或null","conclusions_zh":"...或null","highlights_zh":["...","...","..."]或null}}]}}

论文数据：
{payload}
"""

_SPACED_ABSTRACT = re.compile(r"(?i)\bA\s+B\s+S\s+T\s+R\s+A\s+C\s+T\b")
_SPACED_KEYWORDS = re.compile(r"(?i)\bK\s+E\s+Y\s+W\s+O\s+R\s+D\s+S\b")
_SECTION_HEADING = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+)*[.\s]*)?"
    r"(abstract|摘要|results?|结果|discussion|讨论|conclusions?|结论)\s*[:：]?\s*$"
)
_NEXT_MAJOR_HEADING = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+)*[.\s]*)?"
    r"(?:keywords?|key\s*words|index terms|introduction|background|methods?|materials?|"
    r"results?|discussion|conclusions?|references|关键词|引言|背景|方法|结果|讨论|结论|参考文献)\s*[:：]?\s*$"
)


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {"[]", "{}", "null"}
    if isinstance(value, (list, dict)):
        return not value
    return False


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_extracted_text(value: str | None) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACED_ABSTRACT.sub("ABSTRACT", text)
    return _SPACED_KEYWORDS.sub("KEYWORDS", text)


def extract_explicit_abstract(value: str | None) -> str | None:
    """Extract an explicitly labelled abstract; never synthesize one."""
    text = normalize_extracted_text(value)
    starts = list(re.finditer(r"(?im)(?:^|\n|\s)abstract\s*[:：—-]?\s*", text))
    for match in starts[:3]:
        tail = text[match.end(): match.end() + 7000]
        stop = re.search(
            r"(?im)(?:\n|\s{2,})(?:keywords?|key\s*words|index terms|"
            r"1\.?\s*(?:introduction|background)|introduction|background)\b",
            tail,
        )
        candidate = _clean_text(tail[: stop.start() if stop else 3500])
        if 150 <= len(candidate) <= 5000:
            return candidate
    return None


def _section_excerpt(text: str, names: set[str], limit: int) -> str | None:
    for match in _SECTION_HEADING.finditer(text):
        if match.group(1).casefold() not in names:
            continue
        tail = text[match.end():]
        stop = _NEXT_MAJOR_HEADING.search(tail)
        candidate = _clean_text(tail[: stop.start() if stop else limit])[:limit]
        if len(candidate) >= 30:
            return candidate
    return None


def build_evidence(paper: dict[str, Any], max_chars: int = 4200) -> tuple[str, str]:
    if not is_missing(paper.get("abstract_zh")):
        return "已有中文摘要", str(paper["abstract_zh"])[:max_chars]
    if not is_missing(paper.get("abstract")):
        return "原文摘要", str(paper["abstract"])[:max_chars]

    text = normalize_extracted_text(paper.get("extracted_text"))
    explicit = extract_explicit_abstract(text)
    if explicit:
        return "原文摘要", explicit[:max_chars]

    parts: list[str] = []
    section_specs = (
        ("Results", {"result", "results", "结果"}, 1200),
        ("Discussion", {"discussion", "讨论"}, 1200),
        ("Conclusion", {"conclusion", "conclusions", "结论"}, 1200),
    )
    for label, names, limit in section_specs:
        excerpt = _section_excerpt(text, names, limit)
        if excerpt:
            parts.append(f"[{label}] {excerpt}")
    if not parts:
        # Bounded fallback for papers without recoverable headings.  This is an
        # evidence preview, not an English abstract and is never stored as one.
        parts.append(f"[开篇证据] {_clean_text(text[:2200])}")
        conclusion_hit = re.search(r"(?i)\b(?:in conclusion|in summary|we (?:found|showed|demonstrated))\b", text)
        if conclusion_hit:
            start = max(0, conclusion_hit.start() - 200)
            parts.append(f"[结论附近] {_clean_text(text[start:start + 1400])}")
    return "正文证据片段", "\n".join(parts)[:max_chars]


def extract_json_object(response: str) -> dict[str, Any] | None:
    response = response.strip()
    try:
        parsed = json.loads(response)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", response, re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    block = re.search(r"\{.*\}", response, re.DOTALL)
    if block:
        try:
            parsed = json.loads(block.group())
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def valid_text(value: Any, minimum: int, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if minimum <= len(value) <= maximum else None


def valid_highlights(value: Any) -> list[str] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    items = [item.strip() for item in value if isinstance(item, str) and 10 <= len(item.strip()) <= 160]
    return items if len(items) == 3 else None


def load_client(model_override: str | None = None) -> tuple[OpenAICompatibleClient, str]:
    config = ConfigLoader(ROOT / "config").load_all()
    provider = config.get("runtime", {}).get("provider", {})
    routing = config.get("models", {}).get("routing", {})
    sequence = routing.get("default_auto_sequence", [])
    model = model_override or (sequence[0] if sequence else "example-chat-model")
    client = OpenAICompatibleClient(
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout_seconds=300,
        max_retries=3,
    )
    return client, model


def paper_to_dict(paper: Paper) -> dict[str, Any]:
    return {
        "id": paper.id,
        "internal_id": paper.internal_id,
        "title": paper.title,
        "title_zh": paper.title_zh,
        "abstract": paper.abstract,
        "abstract_zh": paper.abstract_zh,
        "conclusions_zh": paper.conclusions_zh,
        "highlights_zh": paper.highlights_zh,
        "extracted_text": paper.extracted_text,
    }


def load_candidates(entity_label: str, internal_ids: set[int] | None = None) -> list[dict[str, Any]]:
    with get_session(str(ROOT), "config") as session:
        query = session.query(Paper).filter(
            Paper.entity_label == entity_label,
            Paper.internal_id.isnot(None),
        )
        if internal_ids:
            query = query.filter(Paper.internal_id.in_(internal_ids))
        papers = [paper_to_dict(paper) for paper in query.order_by(Paper.internal_id).all()]
        fields = ("abstract", "abstract_zh", "conclusions_zh", "highlights_zh")
        return [paper for paper in papers if any(is_missing(paper[field]) for field in fields)]


def update_explicit_abstracts(papers: list[dict[str, Any]], dry_run: bool) -> int:
    extracted: list[tuple[int, str]] = []
    for paper in papers:
        if is_missing(paper["abstract"]):
            abstract = extract_explicit_abstract(paper["extracted_text"])
            if abstract:
                extracted.append((paper["id"], abstract))
                paper["abstract"] = abstract
    if extracted and not dry_run:
        with get_session(str(ROOT), "config") as session:
            for paper_id, abstract in extracted:
                current = session.query(Paper).filter(Paper.id == paper_id).one()
                if is_missing(current.abstract):
                    current.abstract = abstract
            session.commit()
    return len(extracted)


def missing_fields(paper: dict[str, Any]) -> list[str]:
    return [
        field for field in ("abstract_zh", "conclusions_zh", "highlights_zh")
        if is_missing(paper.get(field))
    ]


def apply_batch_results(
    batch: list[dict[str, Any]], results: dict[str, Any], dry_run: bool,
) -> tuple[int, int, list[dict[str, Any]]]:
    allowed = {paper["id"]: paper for paper in batch}
    updates: list[tuple[int, dict[str, Any]]] = []
    audit_rows: list[dict[str, Any]] = []
    invalid = 0
    raw_items = results.get("papers", [])
    if not isinstance(raw_items, list):
        return 0, len(batch), []

    for item in raw_items:
        if not isinstance(item, dict) or item.get("id") not in allowed:
            continue
        paper = allowed[item["id"]]
        fields: dict[str, Any] = {}
        if "abstract_zh" in missing_fields(paper):
            value = valid_text(item.get("abstract_zh"), 30, 1600)
            if value:
                fields["abstract_zh"] = value
        if "conclusions_zh" in missing_fields(paper):
            value = valid_text(item.get("conclusions_zh"), 20, 360)
            if value:
                fields["conclusions_zh"] = value
        if "highlights_zh" in missing_fields(paper):
            value = valid_highlights(item.get("highlights_zh"))
            if value:
                fields["highlights_zh"] = value
        if not fields:
            invalid += 1
            continue
        updates.append((paper["id"], fields))
        audit_rows.append({"id": paper["id"], "internal_id": paper["internal_id"], "fields": fields})

    if updates and not dry_run:
        with get_session(str(ROOT), "config") as session:
            for paper_id, fields in updates:
                current = session.query(Paper).filter(Paper.id == paper_id).one()
                for field, value in fields.items():
                    if is_missing(getattr(current, field)):
                        setattr(current, field, value)
            session.commit()
    return len(updates), invalid, audit_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    parser.add_argument("--entity-label", default=get_target_label(), help="限定实体；可传空字符串处理全部")
    parser.add_argument("--internal-ids", help="Comma-separated internal IDs")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--extract-only", action="store_true", help="Only extract explicit English abstracts")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))
    if args.entity_label is None:
        parser.error("未配置目标实体，请使用 --entity-label 指定实体或 --entity-label '' 处理全部")

    internal_ids = None
    if args.internal_ids:
        internal_ids = {int(value.strip()) for value in args.internal_ids.split(",") if value.strip().isdigit()}

    papers = load_candidates(args.entity_label, internal_ids)
    if args.limit:
        papers = papers[:args.limit]
    print(f"candidates={len(papers)}", flush=True)
    extracted_count = update_explicit_abstracts(papers, args.dry_run)
    print(f"explicit_abstracts_extracted={extracted_count}", flush=True)
    if args.extract_only or not papers:
        return 0

    todo = [paper for paper in papers if missing_fields(paper)]
    if not todo:
        print("No LLM fields need enrichment")
        return 0

    client, model = load_client(args.model)
    if not client.available():
        raise RuntimeError("LLM API key is not configured")
    print(f"model={model} llm_candidates={len(todo)} batch_size={args.batch_size}", flush=True)

    audit_dir = ROOT / "data" / "enrichment"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / f"summary_enrichment_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    updated_total = 0
    invalid_total = 0
    for start in range(0, len(todo), args.batch_size):
        batch = todo[start:start + args.batch_size]
        payload = []
        for paper in batch:
            evidence_type, evidence = build_evidence(paper)
            payload.append({
                "id": paper["id"],
                "internal_id": paper["internal_id"],
                "title": paper["title_zh"] or paper["title"],
                "missing_fields": missing_fields(paper),
                "evidence_type": evidence_type,
                "evidence": evidence,
            })
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT.format(payload=json.dumps(payload, ensure_ascii=False))},
        ]
        try:
            response = client.chat(
                model, messages, temperature=0.1, max_tokens=6000,
                timeout_override=300, thinking=False, response_format="json_object",
            )
            parsed = extract_json_object(response)
            if parsed is None:
                raise ValueError("LLM returned invalid JSON")
            updated, invalid, audit_rows = apply_batch_results(batch, parsed, args.dry_run)
            updated_total += updated
            invalid_total += invalid
            if audit_rows:
                with audit_path.open("a", encoding="utf-8") as handle:
                    for row in audit_rows:
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"[{min(start + args.batch_size, len(todo))}/{len(todo)}] updated={updated} invalid={invalid}",
                flush=True,
            )
        except Exception as exc:
            invalid_total += len(batch)
            print(f"[{start + 1}-{start + len(batch)}] failed: {exc}", flush=True)
        time.sleep(0.2)

    print(f"done updated={updated_total} invalid_or_failed={invalid_total} audit={audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
