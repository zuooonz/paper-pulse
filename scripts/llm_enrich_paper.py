#!/usr/bin/env python3
"""
LLM 补全论文结构化字段。

从摘要或 PDF 全文提取:
  abstract → abstract_zh, keywords, keywords_zh, research_fields, paradigm, population
  pdf      → sample_size, equipment_brand, channel_count, uses_target_software

用法:
  python scripts/llm_enrich_paper.py --paper-id 6982                  # 单篇
  python scripts/llm_enrich_paper.py --source abstract --paper-id 6982 # 仅摘要
  python scripts/llm_enrich_paper.py --source pdf --paper-id 6982      # 仅 PDF
  python scripts/llm_enrich_paper.py --pending-review                  # 全部待审核
  python scripts/llm_enrich_paper.py --dry-run
"""
import logging

import argparse
import json
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from core.entity_detector import get_target_label, load_entities_config
from core.llm_client import OpenAICompatibleClient
from core.config_loader import ConfigLoader
from core.domain_config import load_domain
from core.taxonomy_config import dimension_items, load_taxonomy_config

# ── Prompts ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a research assistant extracting structured metadata from academic papers.
Return ONLY valid JSON, no markdown, no explanation."""

PROMPT_ABSTRACT = """Extract the following from this paper abstract. Use Chinese for Chinese fields.

Domain context: {domain_name}. {domain_intro}
Use the context only when the paper supports it; classify other topics on their own evidence.

Title: {title}
Abstract:
{abstract}

Existing fields (already extracted, for reference):
{existing_fields}

Tag catalog (existing tags used in our database, prefer these when applicable):
{tag_catalog}

Return JSON:
{{
  "title_zh": "中文标题翻译",
  "abstract_zh": "中文摘要翻译 (2-4 sentences)",
  "conclusions_zh": "2-3句话总结核心结论，100字以内",
  "highlights_zh": ["要点1", "要点2", "要点3"],
  "keywords": ["keyword1", "keyword2", ...],
  "keywords_zh": ["关键词1", "关键词2", ...],
  "research_fields": ["研究领域1", "研究领域2"],
  "paradigm": "研究范式 (从配置目录中选择，可多项)",
  "population": "研究人群或研究对象的简明描述",
  "tags": ["标签1", "标签2", ...]
}}

Rules:
- title_zh: faithful translation of the paper title, keep technical terms in English
- abstract_zh: faithful translation, keep technical terms in English
- conclusions_zh: 2-3 sentences summarizing the core findings in Chinese, ~100 characters
- highlights_zh: 3 key highlights/takeaways from the paper in Chinese
- keywords: 3-6 English keywords
- keywords_zh: 3-6 Chinese keywords
- research_fields: Choose only from the configured research topic catalog below. Use [] if none applies.
- research topic catalog: {research_topic_catalog}
- paradigm catalog: {paradigm_catalog}
- paradigm: Be specific. Use null if unclear.
- population: Be specific about clinical condition, age group, sample characteristics. Use null if unclear.
- tags: Classification tags from the catalog above. A paper typically has 2-5 tags. Return empty list [] if uncertain.
    Decision priority:
    1. First check the study POPULATION (patients/participants)
    2. Then check modalities and tools used alongside the primary method in the study
    3. Then check the experimental task
    4. Finally check the research discipline
    CRITICAL RULES:
    - Tags must be EXACT string matches from the tag list above. Do NOT add any prefix or suffix. Return just the tag name.
    - Read the keyword hints carefully: some tags have "ONLY if" conditions that must be satisfied.
    - When in doubt, leave it out. Return [] rather than guessing.
- Only include fields you can extract with confidence. Use null for uncertain fields."""


def get_tag_catalog(session, taxonomy: dict) -> str:
    """从 papers.tags 提取所有已有标签，按频率排序，返回 prompt 可用的文本。"""
    from sqlalchemy import text as sa_text
    import json as _json
    rows = session.execute(
        sa_text("SELECT tags FROM papers WHERE tags IS NOT NULL")
    ).fetchall()
    tag_freq: dict[str, int] = {}
    for (t,) in rows:
        try:
            for tag in _json.loads(t):
                tag = tag.strip()
                if tag:
                    tag_freq[tag] = tag_freq.get(tag, 0) + 1
        except Exception:
            logger.warning('处理失败，已跳过该项（get_tag_catalog）', exc_info=True)
            pass
    tag_items = dimension_items(taxonomy, "tags")
    lines = ["Configured tags (use exact labels; an empty catalog means return no new tags):"]
    lines.extend(f"  {item['label']}" for item in tag_items)
    lines.append("")
    if tag_freq:
        lines.append("Currently used tags in database:")
        sorted_tags = sorted(tag_freq.items(), key=lambda x: -x[1])[:60]
        for tag, count in sorted_tags:
            lines.append(f"  - {tag} ({count} papers)")
    return "\n".join(lines)

PROMPT_PDF = """Extract the following from this paper full-text excerpt. Use Chinese for Chinese fields where appropriate.

Full text:
{text}

Return JSON:
{{
  "sample_size": 123,
  "equipment_brand": [],
  "channel_count": 48,
  "uses_target_software": {uses_target_software_example},
  "device_citation_text": "relevant sentence about equipment",
  "citation_error_type": "型号错误 / 未引用 / 引用不全 / null"
}}

Rules:
- sample_size: total number of participants. Integer or null.
- equipment_brand: manufacturer or product names that occur in the source. Prefer names from the configured entity catalog {entities}; include other explicit names when needed.
- channel_count: number of acquisition channels. Integer or null.
{uses_target_software_rule}
- device_citation_text: the exact sentence that mentions a device or software. Use null if unclear.
- citation_error_type: classify as "未引用" (no citation), "引用不全" (incomplete), "型号错误" (wrong model), or null if citation is correct.
- Only include fields you can extract with confidence. Use null for uncertain fields."""


def enrich_paper(paper_id: int, session, client, source: str = "abstract",
                 model: str | None = None, dry_run: bool = False) -> dict:
    """Enrich a single paper. Returns dict of extracted fields."""
    paper = session.get(Paper, paper_id)
    if not paper:
        return {"error": "Paper not found"}

    result = {}

    # ── Title-only translation (no abstract needed) ──
    if source in ("abstract", "both", "all") and not paper.title_zh and paper.title:
        try:
            resp = client.chat(model=model, messages=[
                {"role": "system", "content": "Return ONLY valid JSON: {\"title_zh\": \"...\"}"},
                {"role": "user", "content": f"Translate this academic paper title to Chinese, keep technical terms in English: {paper.title}"},
            ], temperature=0.2)
            data = json.loads(resp.strip().removeprefix("```json").removesuffix("```").strip())
            if data.get("title_zh"):
                paper.title_zh = data["title_zh"]
                result["title_zh"] = data["title_zh"]
        except Exception:
            logger.warning('处理失败，已跳过该项（enrich_paper）', exc_info=True)
            pass

    # ── Abstract-based extraction ──
    if source in ("abstract", "both", "all"):
        has_abstract = bool(paper.abstract)
        if not has_abstract and not paper.title:
            print("  [abstract] skip: no abstract or title")
        else:
            taxonomy = load_taxonomy_config()
            tag_catalog = get_tag_catalog(session, taxonomy)
            research_topic_catalog = ", ".join(
                str(item["label"]) for item in dimension_items(taxonomy, "research_topics")
            ) or "(none configured)"
            paradigm_catalog = ", ".join(
                str(item["label"]) for item in dimension_items(taxonomy, "paradigms")
            ) or "(none configured)"
            title = paper.title or ""

            # Build existing fields summary for reference
            existing_parts = []
            if paper.keywords:
                existing_parts.append(f"keywords: {', '.join(str(k) for k in paper.keywords[:8])}")
            if paper.population:
                existing_parts.append(f"population: {paper.population}")
            if paper.research_fields:
                rf = paper.research_fields if isinstance(paper.research_fields, list) else [paper.research_fields]
                existing_parts.append(f"research_fields: {', '.join(str(r) for r in rf)}")
            if paper.paradigm:
                pr = paper.paradigm if isinstance(paper.paradigm, list) else [paper.paradigm]
                existing_parts.append(f"paradigm: {', '.join(str(p) for p in pr)}")
            # multimodal_techniques: extract technique names
            mt = paper.multimodal_techniques
            if mt and isinstance(mt, list):
                techs = set()
                for item in mt:
                    if isinstance(item, dict):
                        for t in item.get("techniques", []):
                            techs.add(str(t))
                if techs:
                    existing_parts.append(f"modalities: {', '.join(sorted(techs))}")
            # brain_regions: top regions
            br = paper.brain_regions
            if br and isinstance(br, list) and len(br) > 0:
                existing_parts.append(f"brain_regions: {', '.join(str(b) for b in br[:8])}")
            existing_fields = "\n".join(existing_parts) if existing_parts else "(none)"

            abstract_text = (paper.abstract or "")[:4000]
            if not abstract_text.strip():
                # No abstract: use existing fields as primary input
                abstract_text = f"(No abstract available. Classify based on existing fields below.)\n{existing_fields}"
            prompt = PROMPT_ABSTRACT.format(
                title=title[:500],
                abstract=abstract_text,
                existing_fields=existing_fields,
                tag_catalog=tag_catalog,
                research_topic_catalog=research_topic_catalog,
                paradigm_catalog=paradigm_catalog,
                domain_name=load_domain().get("name", "科研领域"),
                domain_intro=(load_domain().get("prompt") or {}).get("domain_intro", ""),
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            if dry_run:
                print(f"  [abstract] dry-run: would extract from {len(paper.abstract)} chars")
            else:
                try:
                    resp = client.chat(model=model, messages=messages, temperature=0.2)
                    data = json.loads(resp.strip().removeprefix("```json").removesuffix("```").strip())
                    for field in ["title_zh", "abstract_zh", "conclusions_zh", "highlights_zh",
                                   "keywords", "keywords_zh",
                                   "research_fields", "paradigm", "population"]:
                        if field in data and data[field] is not None:
                            if not getattr(paper, field, None):
                                setattr(paper, field, data[field])
                                result[field] = data[field]
                    # tags: 与已有标签合并，去重后写入
                    if "tags" in data and isinstance(data["tags"], list):
                        new_tags = [t.strip() for t in data["tags"] if t and str(t).strip()]
                        if new_tags:
                            import json as _json
                            existing_tags = set()
                            if paper.tags and isinstance(paper.tags, list):
                                existing_tags = set(paper.tags)
                            merged = list(existing_tags | set(new_tags))
                            session.execute(
                                __import__('sqlalchemy').text("UPDATE papers SET tags = :tags WHERE id = :id"),
                                {"tags": _json.dumps(merged, ensure_ascii=False), "id": paper.id},
                            )
                            result["tags"] = merged
                    print(f"  [abstract] OK: {', '.join(result.keys())}")
                except Exception as e:
                    print(f"  [abstract] FAIL: {e}")

    # ── PDF-based extraction ──
    if source in ("pdf", "both", "all"):
        if not paper.extracted_text:
            print("  [pdf] skip: no extracted_text")
        else:
            entity_config = load_entities_config()
            entity_names = []
            for key, entity in entity_config.get("entities", {}).items():
                if not isinstance(entity, dict):
                    continue
                entity_names.append(str(entity.get("label") or key))
                for field in ("devices", "software", "company"):
                    entity_names.extend(str(item) for item in entity.get(field, []) or [])
            entity_names = list(dict.fromkeys(entity_names))
            target_label = get_target_label()
            target_software_example = "true" if target_label else "null"
            target_software_rule = (
                f"- uses_target_software: true only when the paper explicitly mentions software from the configured target entity ({target_label})."
                if target_label else
                "- uses_target_software: null; target software classification is disabled because no target entity is configured."
            )
            prompt = PROMPT_PDF.format(
                text=paper.extracted_text[:6000],
                entities=json.dumps(entity_names, ensure_ascii=False),
                uses_target_software_example=target_software_example,
                uses_target_software_rule=target_software_rule,
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            if dry_run:
                print(f"  [pdf] dry-run: would extract from {len(paper.extracted_text)} chars")
            else:
                try:
                    resp = client.chat(model=model, messages=messages, temperature=0.2)
                    data = json.loads(resp.strip().removeprefix("```json").removesuffix("```").strip())
                    fields = ["sample_size", "equipment_brand", "channel_count",
                              "device_citation_text", "citation_error_type"]
                    if target_label:
                        fields.append("uses_target_software")
                    for field in fields:
                        if field in data and data[field] is not None:
                            if not getattr(paper, field, None):
                                setattr(paper, field, data[field])
                                result[field] = data[field]
                    print(f"  [pdf] OK: {', '.join(result.keys())}")
                except Exception as e:
                    print(f"  [pdf] FAIL: {e}")

    if not dry_run:
        from paper_library.paper_lifecycle import refresh_metadata_complete
        session.flush()
        session.expire(paper, ["authors"])
        refresh_metadata_complete(paper)
    return result


def main():
    parser = argparse.ArgumentParser(description="LLM enrich paper metadata")
    add_db_arguments(parser)
    parser.add_argument("--paper-id", type=int, default=None)
    parser.add_argument("--unreviewed", action="store_true")
    parser.add_argument("--source", default="abstract",
                        choices=["abstract", "pdf", "both"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    # Load config
    config_loader = ConfigLoader(Path(args.config_dir))
    config = config_loader.load_all()
    rt = config.get("runtime", {})
    llm_cfg = rt.get("provider", {}) or {}
    models_cfg = config.get("models", {}) or {}
    model_sequence = models_cfg.get("routing", {}).get("default_auto_sequence", [])
    selected_model = args.model or llm_cfg.get("model") or (model_sequence[0] if model_sequence else "example-chat-model")

    client = OpenAICompatibleClient(
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key", ""),
        timeout_seconds=120,
    )
    if not client.available():
        print("LLM service is not configured; set LLM_BASE_URL and LLM_API_KEY.")
        return

    with get_session(workspace=args.workspace, config_dir=args.config_dir) as session:
        if args.paper_id:
            papers = [session.get(Paper, args.paper_id)]
            if not papers[0]:
                print(f"Paper {args.paper_id} not found")
                return
        elif args.unreviewed:
            papers = session.query(Paper).filter(
                Paper.review_outcome == "unreviewed"
            ).all()
            print(f"Found {len(papers)} unreviewed papers")
        else:
            print("Specify --paper-id or --unreviewed")
            return

        for i, paper in enumerate(papers, 1):
            print(f"\n[{i}/{len(papers)}] id={paper.id} {paper.title[:60] if paper.title else 'N/A'}")
            enrich_paper(
                paper.id, session, client,
                source=args.source, model=selected_model, dry_run=args.dry_run
            )

        if not args.dry_run:
            session.commit()
            print(f"\nDone. Processed {len(papers)} papers.")


if __name__ == "__main__":
    main()
