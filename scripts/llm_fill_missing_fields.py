#!/usr/bin/env python3
"""
LLM 补全论文缺失字段：摘要、研究结论、研究要点。

从论文全文提取文本中，用 LLM 生成缺失的字段并写入数据库。

用法:
  python scripts/llm_fill_missing_fields.py --paper-id 7        # 单篇
  python scripts/llm_fill_missing_fields.py --all                # 全部缺失论文
  python scripts/llm_fill_missing_fields.py --dry-run            # 预览，不实际写入
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.llm_client import OpenAICompatibleClient
from core.config_loader import ConfigLoader

# ── Prompts ────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a research assistant extracting structured metadata from academic papers.
Return ONLY valid JSON, no markdown, no explanation."""

PROMPT_FILL = """Extract the following from this paper full text. Use Chinese for Chinese fields.

Title: {title}
Journal: {journal}
Year: {year}

Full text (first 2000 chars):
{full_text}

{missing_fields_desc}

Return JSON:
{{
  "abstract": "English abstract from the paper (2-4 sentences). If the abstract is clearly present in the text, extract it faithfully. If not found, generate a concise summary based on the full text content.",
  "abstract_zh": "中文摘要翻译 (2-4 sentences)",
  "conclusions_zh": "2-3句话总结核心结论，100字以内",
  "highlights_zh": ["要点1", "要点2", "要点3"]
}}

Rules:
- abstract: Extract the original English abstract if present in the text. If the paper doesn't have a clear abstract section, leave as empty string "".
- abstract_zh: Faithful Chinese translation of the abstract, keep technical terms in English
- conclusions_zh: 2-3 sentences summarizing the core findings in Chinese, ~100 characters
- highlights_zh: 3 key highlights/takeaways from the paper in Chinese
- Only include fields you can extract with confidence. Use null for uncertain fields.
- If the text is pure metadata/headers without real content, return all nulls."""


def load_llm_client(config_dir: str = "config") -> tuple[OpenAICompatibleClient, str]:
    """从 config 加载 LLM 客户端"""
    config_loader = ConfigLoader(Path(config_dir))
    cfg = config_loader.load_all()
    provider = cfg.get("runtime", {}).get("provider", {})
    models = cfg.get("models", {})

    client = OpenAICompatibleClient(
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout_seconds=60,
    )
    # 优先使用 flash 模型（更快），提取摘要不需要深度推理
    routing = models.get("routing", {})
    auto_seq = routing.get("default_auto_sequence", [])
    model_name = auto_seq[1] if len(auto_seq) > 1 else (auto_seq[0] if auto_seq else "example-chat-model")
    return client, model_name


def get_missing_papers(session, paper_id: Optional[int] = None) -> list:
    """获取需要补全字段的论文列表"""
    conditions = [
        "internal_id IS NOT NULL",
        "extracted_text IS NOT NULL",
        "extracted_text != ''",
        "length(extracted_text) > 500",
        "(abstract IS NULL OR abstract = '' OR conclusions_zh IS NULL OR conclusions_zh = '' OR highlights_zh IS NULL OR highlights_zh = '')",
    ]
    if paper_id is not None:
        conditions.append(f"internal_id = {paper_id}")

    where = " AND ".join(conditions)
    rows = session.execute(text(
        f"SELECT id, internal_id, title, journal_name, year, "
        f"abstract, conclusions_zh, highlights_zh, "
        f"substr(extracted_text, 1, 2000) as preview "
        f"FROM papers WHERE {where}"
    )).fetchall()
    return rows


def needs_field(paper_row, field: str) -> bool:
    """检查某个字段是否需要补全"""
    maps = {"abstract": 5, "conclusions_zh": 6, "highlights_zh": 7}
    idx = maps[field]
    val = paper_row[idx]
    return val is None or str(val).strip() == "" or val == "[]"


def build_missing_desc(paper_row) -> str:
    """构建缺失字段的描述"""
    missing = []
    if needs_field(paper_row, "abstract"):
        missing.append("- abstract (English abstract)")
    if needs_field(paper_row, "conclusions_zh"):
        missing.append("- conclusions_zh (Chinese research conclusions)")
    if needs_field(paper_row, "highlights_zh"):
        missing.append("- highlights_zh (Chinese research highlights)")
    if not missing:
        return ""
    return "Missing fields to fill:\n" + "\n".join(missing)


def fill_missing_fields(paper_id: Optional[int] = None, dry_run: bool = False):
    """补全缺失字段"""
    with get_session(str(ROOT), "config") as session:
        papers = get_missing_papers(session, paper_id)
        print(f"找到 {len(papers)} 篇需要补全的论文")
        if not papers:
            return

    client, model_name = load_llm_client()
    print(f"使用模型: {model_name}")

    # 过滤掉不需要补全的
    todo = [(p, build_missing_desc(p)) for p in papers if build_missing_desc(p)]
    print(f"实际需补全: {len(todo)} 篇")

    if not todo:
        print("无需补全")
        return

    success = 0
    lock = threading.Lock()
    total = len(todo)

    def process_one(p, missing_desc):
        """处理单篇论文"""
        pid, internal_id, title, journal, year, _, _, _, preview = p
        prompt = PROMPT_FILL.format(
            title=title or "",
            journal=journal or "",
            year=str(year) if year else "",
            full_text=preview or "",
            missing_fields_desc=missing_desc,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = client.chat(model_name, messages)
            result = extract_json(response)
            if result is None:
                return f"Paper {internal_id}: [FAIL] LLM 返回非 JSON"

            if dry_run:
                return f"Paper {internal_id}: [dry-run] abstract={str(result.get('abstract',''))[:50]}..."

            # 更新数据库
            with get_session(str(ROOT), "config") as session:
                update_sql = "UPDATE papers SET "
                update_parts = []
                params = {"id": pid}

                if needs_field(p, "abstract") and result.get("abstract"):
                    update_parts.append("abstract = :abstract")
                    params["abstract"] = result["abstract"]
                if result.get("abstract_zh"):
                    update_parts.append("abstract_zh = :abstract_zh")
                    params["abstract_zh"] = result["abstract_zh"]
                if needs_field(p, "conclusions_zh") and result.get("conclusions_zh"):
                    update_parts.append("conclusions_zh = :conclusions_zh")
                    params["conclusions_zh"] = result["conclusions_zh"]
                if needs_field(p, "highlights_zh") and result.get("highlights_zh"):
                    update_parts.append("highlights_zh = :highlights_zh")
                    params["highlights_zh"] = json.dumps(result["highlights_zh"], ensure_ascii=False)

                if update_parts:
                    update_sql += ", ".join(update_parts) + " WHERE id = :id"
                    session.execute(text(update_sql), params)
                    session.commit()

            return f"Paper {internal_id}: [OK] abstract_len={len(str(result.get('abstract','')))}"

        except Exception as e:
            return f"Paper {internal_id}: [FAIL] {e}"

    # 并行执行，3 个并发
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(process_one, p, desc): (p[1], p[2]) for p, desc in todo}
        for i, future in enumerate(as_completed(futures), 1):
            msg = future.result()
            with lock:
                if "[OK]" in msg:
                    success += 1
                print(f"[{i}/{total}] {msg}")

    print(f"\n完成: 成功 {success}/{total}")


def extract_json(response: str) -> Optional[dict]:
    """从 LLM 响应中提取 JSON"""
    import re
    # 尝试直接解析
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ```
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试提取 { ... }
    m = re.search(r'\{.*\}', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description="LLM 补全论文缺失字段")
    add_db_arguments(parser)
    parser.add_argument("--paper-id", type=int, help="仅处理指定 internal_id 的论文")
    parser.add_argument("--all", action="store_true", help="处理所有缺失论文")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    if not args.paper_id and not args.all:
        parser.print_help()
        return

    fill_missing_fields(paper_id=args.paper_id, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
