"""补全缺失原文摘要，并可批量生成中文翻译。

顺序：doi_meta → 实时 Crossref 重试 → PubMed → 全文明确 Abstract 段 → LLM 翻译。

用法:
    python scripts/enrich_abstracts.py [选项]

选项:
    --internal-id-range MIN-MAX   可选范围（如 1-675；默认全库）
    --paper-id ID                 仅处理指定数据库 ID
    --limit N                     限制处理数量
    --skip-crossref               跳过实时 Crossref 重试
    --skip-pubmed                 跳过 PubMed 拉取
    --skip-fulltext               跳过全文提取
    --translate                   用 LLM 生成缺失的 abstract_zh
    --translate-only              仅翻译，不执行原文摘要补全
    --translate-limit N           仅限制本次翻译候选数
    --model MODEL                 翻译模型，默认使用配置模型
    --dry-run                     仅统计不写入
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from paper_library.abstract_enrichment import (
    enrich_missing_abstract,
    extract_abstract_from_fulltext,
    fetch_pubmed_abstract,
    needs_chinese_abstract_package,
    translate_abstract_batch_to_chinese,
    translate_abstract_to_chinese,
)
from core.config_loader import ConfigLoader
from core.llm_client import OpenAICompatibleClient
from core.crossref_api import clean_crossref_abstract

POLITE_DELAY = 0.4  # PubMed 请求间隔
MARKUP_RE = re.compile(r"<\/?[A-Za-z][^>]*>|&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);")


# ── JATS XML 清理 ─────────────────────────────────────────

def strip_jats(xml_text: str) -> str:
    """去除 JATS XML 标签，提取纯文本"""
    if not xml_text or "<" not in xml_text:
        return xml_text or ""
    text = xml_text
    # 将 <jats:title> / <Label> 保留为段落标题
    text = re.sub(r"\s*<jats:title>(.*?)</jats:title>\s*", r"\n\n**\1**\n", text)
    text = re.sub(r"\s*<jats:p>\s*", "\n", text)
    text = re.sub(r"\s*</jats:p>\s*", "\n", text)
    # 移除所有其他标签
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML 实体
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    # 清理每行首尾空白
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()


# ── Step 1: 从 doi_meta 提取 ──────────────────────────────

def step0_clean_markup(session, papers, dry_run=False):
    """Remove stored JATS/HTML markup without changing already-clean abstracts."""
    cleaned = 0
    for paper in papers:
        original = paper.abstract or ""
        if not original or not MARKUP_RE.search(original):
            continue
        value = clean_crossref_abstract(original)
        if value and value != original:
            if not dry_run:
                paper.abstract = value
            cleaned += 1
            print(f"  [clean] id={paper.id} internal_id={paper.internal_id}: {len(original)} -> {len(value)}", flush=True)
    return cleaned

def step1_from_doi_meta(session, papers, dry_run=False):
    """从已有 doi_meta JSON 中提取摘要"""
    filled = 0
    for p in papers:
        if p.abstract:
            continue
        meta = p.doi_meta
        if not meta or not isinstance(meta, dict):
            continue
        msg = meta.get("message", meta)
        raw_abstract = msg.get("abstract", "")
        if not raw_abstract or not isinstance(raw_abstract, str):
            continue
        plain = strip_jats(raw_abstract)
        if len(plain) > 30:
            if not dry_run:
                p.abstract = plain
            filled += 1
            print(f"  [doi_meta] id={p.id} internal_id={p.internal_id}: {len(plain)} chars", flush=True)
    return filled


# ── Step 2: 从 PubMed EFetch 拉取 ─────────────────────────

def step2_from_pubmed(session, papers, dry_run=False):
    """从 PubMed 拉取摘要"""
    filled = 0
    missing = 0
    candidates = [p for p in papers if not p.abstract and p.pmid]

    print(f"  候选论文: {len(candidates)} 篇", flush=True)

    for i, p in enumerate(candidates):
        pmid_str = str(int(float(p.pmid))) if p.pmid.replace(".", "").isdigit() else str(p.pmid).strip()

        abstract = fetch_pubmed_abstract(pmid_str)
        if abstract:
            if not dry_run:
                p.abstract = abstract
            filled += 1
            print(f"  [pubmed] id={p.id} internal_id={p.internal_id}: {len(abstract)} chars", flush=True)
        else:
            missing += 1

        if (i + 1) % 20 == 0:
            print(f"  进度: {i + 1}/{len(candidates)} (filled={filled}, missing={missing})", flush=True)

        time.sleep(POLITE_DELAY)

    return filled


def step_live_crossref(session, papers, dry_run=False):
    """For still-missing DOI records, retry the project's default Crossref client."""
    filled = 0
    errors = 0
    candidates = [p for p in papers if not p.abstract and p.doi]
    print(f"  候选论文: {len(candidates)} 篇", flush=True)
    for i, paper in enumerate(candidates, 1):
        original_abstract = paper.abstract
        original_meta = paper.doi_meta
        result = enrich_missing_abstract(
            paper,
            refresh_crossref=True,
            fetch_pubmed=False,
            extract_fulltext=False,
        )
        if result.changed:
            filled += 1
            print(f"  [crossref] id={paper.id} internal_id={paper.internal_id}", flush=True)
        elif result.error:
            errors += 1
        if i % 20 == 0:
            print(f"  进度: {i}/{len(candidates)} (filled={filled}, errors={errors})", flush=True)
        if dry_run:
            paper.abstract = original_abstract
            paper.doi_meta = original_meta
        time.sleep(0.25)
    return filled, errors


# ── Step 3: 从全文提取 Abstract 段落 ──────────────────────

def step3_from_fulltext(session, papers, dry_run=False):
    """从全文提取摘要"""
    filled = 0
    candidates = [p for p in papers if not p.abstract and p.extracted_text]

    print(f"  候选论文: {len(candidates)} 篇", flush=True)

    for p in candidates:
        abstract = extract_abstract_from_fulltext(p.extracted_text)
        if abstract:
            if not dry_run:
                p.abstract = abstract
            filled += 1
            print(f"  [fulltext] id={p.id} internal_id={p.internal_id}: {len(abstract)} chars", flush=True)

    return filled


def step_translate(
    session, papers, model: str, dry_run: bool = False,
    limit: int | None = None, batch_size: int = 6, workers: int = 1,
):
    config = ConfigLoader(ROOT / "config").load_all()
    provider = config.get("runtime", {}).get("provider", {}) or {}
    client = OpenAICompatibleClient(
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout_seconds=300,
        max_retries=3,
    )
    if not client.available():
        raise RuntimeError("LLM API key is not configured")
    candidates = [p for p in papers if p.abstract and needs_chinese_abstract_package(p)]
    if limit:
        candidates = candidates[:limit]
    print(f"  翻译候选: {len(candidates)} 篇，模型: {model}", flush=True)
    translated = 0
    errors = 0
    batches = []
    current = []
    current_chars = 0
    for paper in candidates:
        abstract_len = len(paper.abstract or "")
        if current and (len(current) >= batch_size or current_chars + abstract_len > 30000):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(paper)
        current_chars += abstract_len
    if current:
        batches.append(current)

    if dry_run:
        for batch in batches:
            print(f"  [translate:dry-run] ids={','.join(str(p.id) for p in batch)}", flush=True)
        return 0, 0

    paper_by_id = {paper.id: paper for paper in candidates}

    def run_batch(batch):
        clones = [
            SimpleNamespace(
                id=paper.id,
                abstract=paper.abstract,
                abstract_zh=paper.abstract_zh,
                conclusions_zh=paper.conclusions_zh,
                highlights_zh=paper.highlights_zh,
            )
            for paper in batch
        ]
        batch_results = translate_abstract_batch_to_chinese(clones, client, model=model)
        # A model can omit one item from otherwise valid batch JSON. Retry only
        # those invalid/missing items individually before recording an error.
        for clone in clones:
            result = batch_results[clone.id]
            if not result.changed and result.source == "translation_invalid":
                batch_results[clone.id] = translate_abstract_to_chinese(clone, client, model=model)
        return [
            (
                clone.id, clone.abstract_zh, clone.conclusions_zh,
                clone.highlights_zh, batch_results[clone.id],
            )
            for clone in clones
        ]

    completed = 0

    def apply_rows(rows):
        nonlocal translated, errors, completed
        for paper_id, abstract_zh, conclusions_zh, highlights_zh, result in rows:
            if result.changed:
                target = paper_by_id[paper_id]
                if not target.abstract_zh and abstract_zh:
                    target.abstract_zh = abstract_zh
                if not target.conclusions_zh and conclusions_zh:
                    target.conclusions_zh = conclusions_zh
                if not target.highlights_zh and highlights_zh:
                    target.highlights_zh = highlights_zh
                translated += 1
            else:
                errors += 1
                print(f"  [translate:{result.source}] id={paper_id}: {result.error or ''}", flush=True)
            completed += 1
        session.commit()
        print(f"  进度: {completed}/{len(candidates)} (translated={translated}, errors={errors})", flush=True)

    if workers <= 1:
        for batch in batches:
            apply_rows(run_batch(batch))
    else:
        completed_rows = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="abstract-translate") as pool:
            futures = [pool.submit(run_batch, batch) for batch in batches]
            for future in as_completed(futures):
                completed_rows.extend(future.result())
                print(
                    f"  模型进度: {len(completed_rows)}/{len(candidates)}",
                    flush=True,
                )
        # Keep SQLAlchemy entirely on the owning thread.  In particular, do
        # not commit while worker requests are still active: scoped sessions
        # and connection provisioning are intentionally not thread-safe.
        apply_rows(completed_rows)
    return translated, errors


# ── 主流程 ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="补全缺失摘要")
    add_db_arguments(parser)
    parser.add_argument("--internal-id-range", type=str)
    parser.add_argument(
        "--internal-only",
        action="store_true",
        help="仅处理有 internal_id 的论文",
    )
    parser.add_argument("--paper-id", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-crossref", action="store_true")
    parser.add_argument("--skip-pubmed", action="store_true")
    parser.add_argument("--skip-fulltext", action="store_true")
    parser.add_argument("--translate", action="store_true")
    parser.add_argument("--translate-only", action="store_true")
    parser.add_argument("--translate-limit", type=int)
    parser.add_argument("--model", default=None)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))
    if args.translate_only:
        args.translate = True

    print("=" * 60)
    print("摘要补全")
    print("=" * 60)
    scope = args.internal_id_range or (
        "paper id " + str(args.paper_id) if args.paper_id
        else ("有 internal_id 的论文" if args.internal_only else "全库")
    )
    print(f"范围: {scope}")
    if args.dry_run:
        print("模式: dry-run")
    print()

    with get_session(str(ROOT), "config") as session:
        # 加载目标论文
        query = session.query(Paper).filter(Paper.title.isnot(None))
        if args.internal_only:
            query = query.filter(Paper.internal_id.isnot(None))
        if args.internal_id_range:
            parts = args.internal_id_range.split("-")
            id_min, id_max = int(parts[0]), int(parts[1])
            query = query.filter(Paper.internal_id >= id_min, Paper.internal_id <= id_max)
        if args.paper_id:
            query = query.filter(Paper.id == args.paper_id)
        query = query.order_by(Paper.id)
        if args.limit:
            query = query.limit(args.limit)
        papers = query.all()

        total = len(papers)
        has_abstract_before = sum(1 for p in papers if p.abstract)
        print(f"论文总数: {total}")
        print(f"已有摘要: {has_abstract_before}")
        print(f"缺失摘要: {total - has_abstract_before}")
        print()

        s0 = s1 = sx = ex = s2 = s3 = 0
        if not args.translate_only:
            print("Step 0: 清理 JATS/HTML 摘要...")
            s0 = step0_clean_markup(session, papers, args.dry_run)
            print(f"  → 清理 {s0} 篇")
            print()

            # Step 1: 从 doi_meta 提取
            print("Step 1: 从 doi_meta 提取...")
            s1 = step1_from_doi_meta(session, papers, args.dry_run)
            print(f"  → 补全 {s1} 篇")
            print()

            # Step 2: 实时 Crossref 重试
            if not args.skip_crossref:
                print("Step 2: 实时 Crossref 重试...")
                sx, ex = step_live_crossref(session, papers, args.dry_run)
                print(f"  → 补全 {sx} 篇，错误 {ex} 篇")
            else:
                print("Step 2: 跳过（--skip-crossref）")
            print()

            # Step 3: 从 PubMed 拉取
            if not args.skip_pubmed:
                print("Step 3: 从 PubMed EFetch 拉取...")
                s2 = step2_from_pubmed(session, papers, args.dry_run)
                print(f"  → 补全 {s2} 篇")
            else:
                print("Step 3: 跳过（--skip-pubmed）")
            print()

            # Step 4: 从全文提取
            if not args.skip_fulltext:
                print("Step 4: 从全文提取...")
                s3 = step3_from_fulltext(session, papers, args.dry_run)
                print(f"  → 补全 {s3} 篇")
            else:
                print("Step 4: 跳过（--skip-fulltext）")
            print()

        # 提交
        if not args.dry_run:
            session.commit()
            print("已提交到数据库。")

        st = et = 0
        if args.translate:
            print("\nStep 5: LLM 中文翻译...")
            model = args.model
            if not model:
                config = ConfigLoader(ROOT / "config").load_all()
                runtime = config.get("runtime", {}) or {}
                models = config.get("models", {}) or {}
                sequence = models.get("routing", {}).get("default_auto_sequence", [])
                model = runtime.get("abstract_enrichment", {}).get("model") or (sequence[0] if sequence else "example-chat-model")
            st, et = step_translate(
                session, papers, model, args.dry_run,
                args.translate_limit or args.limit,
                batch_size=max(1, args.batch_size),
                workers=max(1, args.workers),
            )
            print(f"  → 翻译 {st} 篇，错误 {et} 篇")

    # 统计
    has_abstract_after = has_abstract_before + s1 + sx + s2 + s3
    print()
    print("=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"Step 1 (doi_meta):  {s1}")
    print(f"Step 2 (Crossref):  {sx}")
    print(f"Step 3 (PubMed):    {s2}")
    print(f"Step 4 (全文提取):  {s3}")
    print(f"Step 5 (中文翻译):  {st}")
    print(f"总计补全:           {s1 + sx + s2 + s3}")
    print(f"摘要覆盖率: {has_abstract_before}/{total} → {has_abstract_after}/{total} ({100 * has_abstract_after / total:.1f}%)")


if __name__ == "__main__":
    main()
