import logging

# 批量生成论文中文摘要（conclusions_zh + highlights_zh）
import sys, json, time, argparse

from pathlib import Path
logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from core.config_loader import ConfigLoader
from core.llm_client import OpenAICompatibleClient

SYSTEM_PROMPT = "你是专业的科研论文分析助手，用简洁的中文总结英文学术论文。"

def make_user_prompt(paper: Paper) -> str:
    abstract = paper.abstract or ""
    text_excerpt = (paper.extracted_text or "")[:1500]
    content = abstract if abstract else text_excerpt
    if not content:
        return ""
    return (
        f"论文标题：{paper.title}\n\n"
        f"摘要/正文节选：{content}\n\n"
        "请输出以下 JSON（不加 markdown 代码块）：\n"
        '{"conclusions_zh": "2-3句话总结核心结论，100字以内", '
        '"highlights_zh": ["要点1", "要点2", "要点3"]}'
    )

def parse_llm_response(text: str) -> dict | None:
    text = text.strip()
    # 去除可能的 ```json ... ``` 包裹
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        data = json.loads(text)
        if "conclusions_zh" in data and "highlights_zh" in data:
            return data
    except Exception:
        logger.warning('处理失败，已跳过该项（parse_llm_response）', exc_info=True)
        pass
    return None

def main():
    setup_console()
    parser = argparse.ArgumentParser(description="批量生成中文摘要")
    add_db_arguments(parser)
    parser.add_argument("--limit", type=int, default=10, help="本次最多处理篇数")
    parser.add_argument("--paper-id", type=int, default=None, help="指定单篇论文 DB id")
    parser.add_argument("--dry-run", action="store_true", help="只打印 prompt，不调用 LLM")
    parser.add_argument("--workspace", default=".", help="工作目录")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    parser.add_argument("--sleep", type=float, default=1.0, help="每篇之间等待秒数（限速）")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    # 加载配置
    cfg = ConfigLoader(Path(args.workspace) / args.config_dir).load_all()
    runtime = cfg["runtime"]
    domain = cfg.get("domain", {})
    domain_name = domain.get("name") or domain.get("short_name") or "科研领域"
    domain_intro = (domain.get("prompt") or {}).get("domain_intro", "")
    system_prompt = f"{SYSTEM_PROMPT}\n当前领域：{domain_name}。{domain_intro}"
    models_cfg = cfg.get("models", {})

    llm = OpenAICompatibleClient(
        base_url=runtime["provider"]["base_url"],
        api_key=runtime["provider"]["api_key"],
    )
    routing = models_cfg.get("routing", {})
    auto_seq = routing.get("default_auto_sequence", [])
    all_models = [m["id"] for m in models_cfg.get("models", []) if isinstance(m, dict)]
    model_name = auto_seq[0] if auto_seq else (all_models[0] if all_models else models_cfg.get("default_model", ""))
    if not model_name:
        print("ERROR: no model configured"); return

    print(f"Model: {model_name}  limit={args.limit}  dry_run={args.dry_run}")

    stats = {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    # Get list of papers first
    with get_session(args.workspace, args.config_dir) as session:
        stmt = select(Paper).where(
            Paper.conclusions_zh.is_(None),
            (Paper.abstract.is_not(None) | Paper.extracted_text.is_not(None))
        )
        if args.paper_id:
            stmt = stmt.where(Paper.id == args.paper_id)
        else:
            stmt = stmt.limit(args.limit)
        papers = list((p.id, p.title, p.abstract, p.extracted_text) for p in session.scalars(stmt).all())
    # Dispose after getting list
    from paper_library.db import _engine
    if _engine is not None:
        _engine.dispose()
        # Reset global to force new connection
        _engine = None
        _session_factory = None

    print(f"Found {len(papers)} papers without conclusions_zh (with abstract/extracted_text)")

    for (paper_id, title, abstract, extracted_text) in papers:
        stats["processed"] += 1
        content = abstract if abstract else (extracted_text or "")[:1500]
        if not content:
            print(f"  [SKIP] id={paper_id} no abstract/text")
            stats["skipped"] += 1
            continue

        print(f"  [{stats['processed']}] id={paper_id} {title[:50]}...")

        prompt = (
            f"论文标题：{title}\n\n"
            f"摘要/正文节选：{content}\n\n"
            "请输出以下 JSON（不加 markdown 代码块）：\n"
            '{"conclusions_zh": "2-3句话总结核心结论，100字以内", '
            '"highlights_zh": ["要点1", "要点2", "要点3"]}'
        )

        if args.dry_run:
            print(f"    [DRY] prompt length={len(prompt)}")
            stats["success"] += 1
            continue

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            response = llm.chat(model_name, messages)
            parsed = parse_llm_response(response)
            if parsed:
                # Open a new session for each paper to avoid locking
                with get_session(args.workspace, args.config_dir) as session:
                    paper = session.get(Paper, paper_id)
                    if paper:
                        paper.conclusions_zh = parsed["conclusions_zh"]
                        paper.highlights_zh = parsed["highlights_zh"]
                        session.commit()
                        print(f"    [OK] conclusions={parsed['conclusions_zh'][:50]}...")
                        stats["success"] += 1
                    else:
                        print(f"    [WARN] paper id={paper_id} not found")
                        stats["failed"] += 1
            else:
                print(f"    [WARN] parse failed, raw: {response[:100]}")
                stats["failed"] += 1
        except Exception as e:
            print(f"    [ERROR] {e}")
            stats["failed"] += 1

        # Dispose after each paper to release lock
        from paper_library.db import _engine
        if _engine is not None:
            _engine.dispose()
            # Reset global
            import paper_library.db
            paper_library.db._engine = None
            paper_library.db._session_factory = None

        if args.sleep > 0:
            time.sleep(args.sleep)

    print("\n=== Summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    main()
