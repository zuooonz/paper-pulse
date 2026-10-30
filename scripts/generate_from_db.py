"""批量从 DB 读取论文数据生成推文草稿

Usage:
    python scripts/generate_from_db.py [--limit N] [--paper-id ID] [--dry-run]
                                   [--workspace .] [--config-dir config]
                                   [--profile default] [--themes "临床,成像方法"]
"""
import logging

import argparse
import re
from pathlib import Path
from typing import List, Dict

from docx import Document
from docx.shared import Pt, Cm
from docx.oxml.ns import qn

from sqlalchemy import select

# Add src to path
import sys
logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.config_loader import ConfigLoader
from core.llm_client import OpenAICompatibleClient
from core.article_builder import build_messages
from core.entity_detector import get_target_label
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper, ArticleDraft
from paper_library.cross_db import link_draft_to_paper
from tools.storage import get_image_root, get_output_dir


def paper_to_rec(paper: Paper) -> Dict[str, str]:
    """将 Paper 字段映射为 article_builder 期望的键名"""
    jcr_quartile = ""
    impact_factor = ""
    if paper.journal and paper.journal.metrics:
        latest = sorted(paper.journal.metrics, key=lambda m: m.year, reverse=True)
        if latest:
            metric = latest[0]
            impact_factor = str(metric.impact_factor) if metric.impact_factor is not None else ""
            jcr_quartile = metric.jcr_quartile if metric.jcr_quartile else ""

    # Device context
    device_text = paper.device_citation_text or ""
    entity_label = paper.entity_label or ""
    target_label = get_target_label()
    if paper.cites_target and entity_label == target_label and target_label:
        device_text = f"论文资料确认提及目标实体。{device_text}"

    # APA citation from CrossRef
    apa_citation = ""
    if paper.doi:
        try:
            from core.crossref_api import fetch_citation_by_doi
            apa_citation = fetch_citation_by_doi(paper.doi) or ""
        except Exception:
            logger.warning('处理失败，已跳过该项（paper_to_rec）', exc_info=True)
            pass

    return {
        "论文编号": str(paper.internal_id or paper.id),
        "API权威标题": paper.title or "",
        "英文标题": paper.title or "",
        "API权威期刊": paper.journal_name or "",
        "期刊名称": paper.journal_name or "",
        "API发表日期": str(paper.year or ""),
        "DOI": paper.doi or "",
        "IF": impact_factor,
        "JCR分区": jcr_quartile,
        "PMID": paper.pmid or "",
        "apa_citation": apa_citation,
        "entity_label": paper.entity_label or "",
        "使用设备": "" if entity_label and entity_label == target_label else entity_label,
        "设备引用信息": device_text,
    }


def _extract_title(content: str, fallback: str = "") -> str:
    """Extract title from generated content, with safety fallback."""
    lines = content.strip().splitlines()
    for line in lines:
        s = line.strip()
        # Markdown heading
        if s.startswith("#"):
            raw = s.lstrip("#").strip()
            if raw and raw not in ("标题", "标题。", "(标题)", "标题："):
                return raw
            break
        # 【实体标签】... format (collection template)
        if s.startswith("【") and "】" in s:
            if s not in ("【实体】", "【实体合集】", "【合集主题】"):
                return s
    return fallback


def slugify_title(title: str, max_length: int = 30) -> str:
    """将标题转换为文件名友好的 slug"""
    if not title:
        return "untitled"
    # 替换非字母数字为连字符
    slug = re.sub(r'[^\w\s-]', '', title.lower())
    slug = re.sub(r'[\s]+', '-', slug).strip('-')
    return slug[:max_length]


def generate_for_paper(
    paper: Paper,
    session,
    draft_session,
    llm: OpenAICompatibleClient,
    model_name: str,
    prompts_cfg: Dict,
    themes: List[str],
    output_dir: Path | None,
    profile: str = "default",
    profile_data: Dict | None = None,
    dry_run: bool = False,
    word_output: bool = False,
    get_model_config: callable = None,
    output_root: Path | None = None,
    workspace: str | Path = ".",
) -> None:
    """为单篇论文生成推文并保存"""
    rec = paper_to_rec(paper)
    extracted_text = paper.extracted_text or ""

    # Count extracted images if available
    image_count = 0
    if paper.extracted_images and isinstance(paper.extracted_images, list):
        image_count = len(paper.extracted_images)

    messages = build_messages(rec, themes, prompts_cfg, extracted_text, image_count,
                              profile=profile, profile_data=profile_data,
                              cites_target=paper.cites_target)

    if dry_run:
        print(f"[DRY RUN] Would generate: paper_id={paper.id} title={paper.title[:60]}...")
        return

    print(f"Generating for paper {paper.id}: {paper.title[:60]}...")
    _get = get_model_config or (lambda _: {})
    content = llm.chat(model_name, messages, thinking=_get(model_name).get("thinking"))

    # Optionally save to output file
    if output_dir or word_output:
        slug = slugify_title(paper.title or "untitled")
        base = (output_dir or output_root or get_output_dir(workspace)) / f"{paper.id}_{slug}"
        if output_dir:
            (base.parent / f"{base.name}.md").write_text(content, encoding="utf-8")
            print(f"  Saved to: {base}.md")
        if word_output:
            markdown_to_docx(content, [(paper.title or "", [])],
                             base.parent / f"{base.name}.docx")
            print(f"  Saved to: {base}.docx")
            _copy_image_folder(paper.id, paper.title or "", base.parent, workspace)

    chinese_title = _extract_title(content, paper.title or f"Paper {paper.id}")

    # Create ArticleDraft record in the application database.
    draft = ArticleDraft(
        draft_type="single_daily",
        title=chinese_title,
        content=content,
        status="draft",
        meta={
            "paper_id": paper.id,
            "model": model_name,
            "profile": profile,
        }
    )
    draft_session.add(draft)
    draft_session.flush()  # get draft.id
    link_draft_to_paper(draft, paper, draft_session)
    paper.article_status = "generated"

def _is_paper_heading(text: str) -> bool:
    """Return True if this heading looks like an English paper title (not a Chinese section label)."""
    # English paper titles are long and mostly ASCII
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return len(text) > 30 and ascii_chars > len(text) * 0.6


def _find_image_group(title_text: str, paper_images: list[tuple[str, list[Path]]]) -> int | None:
    """Find the paper_images index whose label best matches the given title text."""
    title_lower = title_text.lower()
    best_idx, best_score = None, 0
    for idx, (label, _) in enumerate(paper_images):
        label_lower = label.lower()
        score = sum(1 for w in title_lower.split() if len(w) > 3 and w in label_lower)
        if score > best_score:
            best_score, best_idx = score, idx
    return best_idx


def _set_run_font(run, size: Pt | None = None):
    """Set Chinese-compatible font on a run."""
    run.font.name = '微软雅黑'
    rpr = run._element.get_or_add_rPr()
    rFonts = rpr.find(qn('w:rFonts'))
    if rFonts is None:
        from lxml import etree
        rFonts = etree.SubElement(rpr, qn('w:rFonts'))
    rFonts.set(qn('w:eastAsia'), '微软雅黑')
    if size:
        run.font.size = size


def _add_bold_runs(paragraph, text: str):
    """Add runs to paragraph, handling **bold** markers."""
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        else:
            run = paragraph.add_run(part)
        _set_run_font(run)


def _strip_markdown(text: str) -> str:
    """Remove residual Markdown syntax before Word conversion."""
    # Remove horizontal rules
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^___+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\*\*\*+\s*$', '', text, flags=re.MULTILINE)
    # Remove stray blockquote markers at line start
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Remove inline code backticks
    text = re.sub(r'(?<!\w)`([^`]+)`(?!\w)', r'\1', text)
    # Remove single * italic markers (but keep ** bold — handled later)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', text)
    return text


def markdown_to_docx(md_text: str, paper_images: list[tuple[str, list[Path]]],
                     output_path: Path) -> None:
    """Convert Markdown article to .docx with images placed inline after '使用设备'."""
    md_text = _strip_markdown(md_text)
    lines = md_text.splitlines()
    current_img_group: int | None = None
    inserted_current = False
    paper_seq = 0

    # Phase 1: build intermediate list of paragraph-like items
    items: list[tuple] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[插入原图：') or stripped.startswith('[插入原图:'):
            continue
        if not stripped:
            continue

        if stripped.startswith('【') and '】' in stripped:
            items.append(("title", stripped))
            continue

        if stripped.startswith('## '):
            items.append(("h2", stripped[3:]))
            continue

        if stripped.startswith('### '):
            heading_text = stripped[4:]
            items.append(("h3", heading_text))
            # Only match against paper_images if this looks like an English paper title
            # (Chinese/label-only headings in 研究速览 are not paper sections)
            if _is_paper_heading(heading_text):
                inserted_current = False
                matched = _find_image_group(heading_text.strip().lower(), paper_images)
                current_img_group = matched if matched is not None else paper_seq
                paper_seq += 1
            continue

        if stripped.startswith('**') and '：' in stripped:
            items.append(("bold_field", stripped))
            if ('使用设备' in stripped and not inserted_current
                    and current_img_group is not None and current_img_group < len(paper_images)):
                _, paths = paper_images[current_img_group]
                for pth in paths:
                    if pth.exists():
                        items.append(("image", pth))
                inserted_current = True
            continue

        items.append(("text", stripped))

    # Phase 2: render items to docx (text only, no images)
    doc = Document()
    style = doc.styles['Normal']
    style.font.size = Pt(11)
    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    for item in items:
        kind = item[0]
        text = str(item[1])

        if kind == "image":
            continue  # images go to separate folders

        elif kind == "title":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True; _set_run_font(run, Pt(18))

        elif kind == "h2":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True; _set_run_font(run, Pt(15))

        elif kind == "h3":
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold = True; _set_run_font(run, Pt(13))

        elif kind == "bold_field":
            p = doc.add_paragraph()
            _add_bold_runs(p, text)

        else:  # text
            p = doc.add_paragraph()
            _add_bold_runs(p, text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))

def _safe_folder_name(title: str, paper_id: int, max_len: int = 80) -> str:
    """Convert a paper title to a safe folder name."""
    # Remove illegal characters
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
    safe = re.sub(r'\s+', ' ', safe).strip()
    if len(safe) > max_len:
        safe = safe[:max_len].rsplit(' ', 1)[0]
    return safe or f"paper_{paper_id}"


def _copy_image_folder(paper_id: int, title: str, dest_root: Path,
                       workspace: str | Path = "."):
    """Copy paper's image directory to dest_root, renamed to the paper title."""
    img_dir = get_image_root(workspace) / str(paper_id)
    if not img_dir.exists():
        return
    folder_name = _safe_folder_name(title, paper_id)
    dest = dest_root / folder_name
    if dest.exists():
        return  # already copied
    dest.mkdir(parents=True, exist_ok=True)
    for f in img_dir.iterdir():
        if f.is_file() and f.stat().st_size >= 10 * 1024:  # filter small images
            import shutil
            shutil.copy2(f, dest / f.name)


def _filter_images(paths: list[Path], min_size_kb: int = 10) -> list[Path]:
    """Filter out images too small to be meaningful (logos, fragments)."""
    result = []
    for p in paths:
        if not p.exists():
            continue
        size_kb = p.stat().st_size / 1024
        if size_kb < min_size_kb:
            continue
        result.append(p)
    return result


def _resolve_images(paper: Paper, workspace: str | Path = ".") -> list[Path]:
    """Resolve all extracted image paths for a paper, filtering small ones."""
    paths = []
    if paper.extracted_images and isinstance(paper.extracted_images, list):
        for img in paper.extracted_images:
            if isinstance(img, dict) and img.get("path"):
                p = Path(img["path"])
                if not p.is_absolute():
                    p = get_image_root(workspace) / str(paper.id) / p.name
                if p.exists():
                    paths.append(p)
    return _filter_images(paths)


def _resolve_images_for_id(paper_id: int, workspace: str | Path = ".") -> list[Path]:
    """Resolve image paths by paper ID (used in collection mode)."""
    img_dir = get_image_root(workspace) / str(paper_id)
    if img_dir.exists():
        return _filter_images(sorted(img_dir.glob("*.*")))
    return []


def _paper_device_info(p: Paper) -> str:
    """Extract device info from Paper DB fields for prompt injection."""
    parts = []
    target_label = get_target_label()
    if p.cites_target and target_label:
        parts.append("论文资料确认提及目标实体")
        if p.uses_target_software:
            parts.append("使用了目标实体软件")
        if p.device_citation_text:
            parts.append(f"设备引用原文: {p.device_citation_text[:200]}")
    return "; ".join(parts)


def _paper_citation(p: Paper) -> str:
    """Build APA 7th citation from CrossRef DOI metadata. Falls back to DB fields."""
    if p.doi:
        from core.crossref_api import fetch_citation_by_doi
        cite = fetch_citation_by_doi(p.doi)
        if cite:
            return cite
    # Fallback: build from DB fields
    parts = []
    first_authors = getattr(p, "first_authors", None)
    if first_authors and isinstance(first_authors, list):
        names = [a.get("name", "") for a in first_authors if isinstance(a, dict) and a.get("name")]
        if names:
            parts.append(names[0] + " et al.")
    if p.year:
        parts.append(f"({p.year})")
    if p.title:
        parts.append(f"{p.title}.")
    if p.journal_name:
        parts.append(f"*{p.journal_name}*")
    if p.volume:
        vp = p.volume
        if p.issue:
            vp += f"({p.issue})"
        if p.pages:
            vp += f", {p.pages}"
        parts.append(vp + ".")
    if p.doi:
        parts.append(f"https://doi.org/{p.doi}")
    return " ".join(parts)


def _paper_team_info(p: Paper) -> str:
    """Extract corresponding author info from Paper DB fields."""
    corresponding_authors = getattr(p, "corresponding_authors", None)
    if corresponding_authors and isinstance(corresponding_authors, list):
        ca = corresponding_authors[0] if isinstance(corresponding_authors[0], dict) else None
        if ca:
            name = ca.get("name", "")
            aff = ca.get("affiliation", "")
            return f"通讯作者: {name}" + (f" ({aff})" if aff else "")
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="从 DB 读取论文数据批量生成推文草稿"
    )
    add_db_arguments(parser)
    parser.add_argument("--limit", type=int, default=None, help="最大生成数量")
    parser.add_argument("--paper-id", type=int, default=None, help="指定单篇论文 ID")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调用 LLM 不写 DB")
    parser.add_argument("--workspace", default=".", help="工作目录路径")
    parser.add_argument("--config-dir", default="config", help="配置文件目录")
    parser.add_argument("--profile", default="default", help="生成配置 profile")
    parser.add_argument("--themes", default="", help="指定主题配置值，逗号分隔")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="每批处理篇数，处理完一批后 commit")
    parser.add_argument("--status-filter", default="not_generated",
                        help="只处理 article_status 为此值的论文（default: not_generated）")
    parser.add_argument("--force-regen", action="store_true",
                        help="允许重新生成已有草稿的论文（忽略 article_status 过滤）")
    parser.add_argument("--save-files", action="store_true",
                        help="额外将生成的 Markdown 保存到 output/ 目录（默认只写入 DB）")
    parser.add_argument("--word", action="store_true",
                        help="将生成的推文输出为 Word (.docx) 文件到 output/ 目录")
    parser.add_argument("--paper-ids", default="",
                        help="按数据库主键 ID 筛选，逗号分隔（如 '1,2,3'）")
    parser.add_argument("--internal-ids", default="",
                        help="按 internal_id（Cite ID）筛选，逗号分隔（如 '191,494,599'）")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    # 加载配置
    loader = ConfigLoader(Path(args.workspace) / args.config_dir)
    cfg = loader.load_all()
    runtime = cfg["runtime"]
    models_cfg = cfg["models"]
    prompts_cfg = cfg["prompts"]
    domain_config = cfg.get("domain", {})
    domain_name = str(domain_config.get("name") or domain_config.get("short_name") or "科研领域")
    domain_intro = str((domain_config.get("prompt") or {}).get("domain_intro") or "")
    profile_data = loader.load_profile(args.profile) if args.profile != "default" else {}

    # 初始化 LLM 客户端
    llm = OpenAICompatibleClient(
        base_url=runtime["provider"]["base_url"],
        api_key=runtime["provider"]["api_key"],
    )
    all_models = models_cfg.get("models", [])
    routing = models_cfg.get("routing", {})
    auto_seq = routing.get("default_auto_sequence", [])
    model_name = auto_seq[0] if auto_seq else (all_models[0]["id"] if all_models else models_cfg.get("default_model", ""))

    def _get_model_config(name: str) -> dict:
        for m in all_models:
            if m["id"] == name:
                return m
        return {}

    # 解析 themes
    themes = [t.strip() for t in args.themes.split(",") if t.strip()] if args.themes else []

    # 创建 output 目录（仅在 --save-files 时）
    output_root = get_output_dir(args.workspace)
    output_dir = output_root if args.save_files else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    # 查询目标论文
    import time
    start = time.time()

    with get_session(args.workspace, args.config_dir) as session:
        stmt = select(Paper).where(
            Paper.processing_status == "done",
            Paper.review_outcome == "approved",
        )
        force_regen = getattr(args, 'force_regen', False)
        if not force_regen:
            status_filter = getattr(args, 'status_filter', 'not_generated')
            stmt = stmt.where(Paper.article_status == status_filter)
        if args.paper_id:
            stmt = stmt.where(Paper.id == args.paper_id)
        if args.paper_ids:
            ids = [int(x.strip()) for x in args.paper_ids.split(",") if x.strip()]
            if ids:
                stmt = stmt.where(Paper.id.in_(ids))
        if args.internal_ids:
            ids = [int(x.strip()) for x in args.internal_ids.split(",") if x.strip()]
            if ids:
                stmt = stmt.where(Paper.internal_id.in_(ids))
        if args.limit:
            stmt = stmt.limit(args.limit)

        papers = list(session.scalars(stmt).all())
        target_label = get_target_label()
        from paper_library.paper_lifecycle import is_usable_for_generation
        from core.domain_config import generation_requires_target_entity
        require_target_entity = generation_requires_target_entity(domain_config)
        papers = [paper for paper in papers if is_usable_for_generation(
            paper, require_target_entity, target_label,
        )]
        print(f"Found {len(papers)} paper(s) ready for generation")

        if not papers:
            print("No papers to process, exiting.")
            return

        # ── Collection mode: N papers → 1 draft ──
        if args.profile == "collection":
            themes = themes or ["临床"]
            theme_str = ", ".join(themes)

            paper_summaries = []
            for p in papers:
                summary_text = p.abstract or p.conclusions_zh or ""
                if not summary_text and p.extracted_text:
                    summary_text = p.extracted_text[:1500]
                device_info = _paper_device_info(p)
                team_info = _paper_team_info(p)
                paper_summaries.append({
                    "id": p.id,
                    "internal_id": p.internal_id,
                    "title": p.title or "",
                    "year": p.year,
                    "journal": p.journal_name or "",
                    "doi": p.doi or "",
                    "authors": [
                        a.get("name", "")
                        for a in (getattr(p, "first_authors", None) or [])
                        if isinstance(a, dict)
                    ],
                    "summary": summary_text,
                    "image_count": len(p.extracted_images) if p.extracted_images and isinstance(p.extracted_images, list) else 0,
                    "device_info": device_info,
                    "team_info": team_info,
                    "citation": _paper_citation(p),
                })

            if len(paper_summaries) < 2:
                print("Collection mode needs at least 2 papers.")
                return

            system_prompt = profile_data.get("review_system_prompt") or prompts_cfg.get("system_prompt", "你是科研编辑。")
            structure = profile_data.get("review_structure") or []

            summaries_block = "\n\n".join([
                f"【论文 {i+1}】\n标题：{p['title']}\n"
                + (f"团队：{p['team_info']}\n" if p.get('team_info') else "")
                + (f"设备：{p['device_info']}\n" if p.get('device_info') else "")
                + f"期刊：{p['journal']} ({p['year']})\nDOI：{p['doi']}\n"
                + f"预构建参考文献：{p['citation']}\n"
                + f"核心内容：\n{p['summary'][:800]}"
                for i, p in enumerate(paper_summaries)
            ])

            target_label = get_target_label()
            has_target_reference = target_label and any(
                p.cites_target and p.entity_label == target_label for p in papers
            )
            target_reference_note = ""
            if has_target_reference:
                target_reference_note = (
                    f"\n\n# 可选实体说明\n论文证据确认提及 {target_label}。"
                    "仅在相关处客观说明其研究作用，不延伸为宣传内容。"
                )

            theme_templates = profile_data.get("theme_templates") or prompts_cfg.get("theme_templates", {})
            theme_lines = []
            for t in themes:
                template = theme_templates.get(t, f"围绕「{t}」主题进行深入解读。")
                theme_lines.append(f"### 主题「{t}」的解读要求：\n{template.strip()}")
            theme_guidance = "\n\n".join(theme_lines)

            structure_block = "\n".join([
                (f"{item['section']}：" if item.get('section') else "")
                + f"{item['guidance']}"
                + (f"（{item['char_range']}）" if item.get('char_range') else "")
                for item in structure
            ])

            user_prompt = (
                f"请基于以下 {len(paper_summaries)} 篇{domain_name}相关学术论文，撰写一篇中文合集推文。\n"
                f"领域背景：{domain_intro}\n\n"
                f"主题侧重：{theme_str}\n\n"
                + (theme_guidance + "\n\n" if theme_guidance else "") +
                f"=== 论文摘要 ===\n{summaries_block}"
                f"{target_reference_note}\n\n"
                f"=== 文章结构要求 ===\n{structure_block}\n\n"
                f"要求：对多篇论文进行横向整合分析，所有数据忠实于原文，使用 Markdown ## 标题。"
            )

            if args.dry_run:
                print(f"[DRY RUN] Would generate collection for {len(paper_summaries)} papers:")
                for p in paper_summaries:
                    print(f"  - [{p['id']}] {p['title'][:60]}")
                return

            print(f"Generating collection for {len(paper_summaries)} papers...")
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            content = llm.chat(model_name, messages, thinking=_get_model_config(model_name).get("thinking"))
            chinese_title = _extract_title(content, f"{domain_name}研究进展合集")

            draft = ArticleDraft(
                draft_type="collection",
                title=chinese_title,
                content=content,
                status="draft",
                meta={
                    "paper_ids": [p["id"] for p in paper_summaries],
                    "model": model_name,
                    "profile": args.profile,
                    "themes": themes,
                },
            )
            session.add(draft)
            session.flush()  # 先取得 draft.id，再写入论文关联
            for ps in paper_summaries:
                paper = session.get(Paper, ps["id"])
                if paper:
                    link_draft_to_paper(draft, paper, session)
                    paper.article_status = "generated"
            session.commit()
            print(f"Done! draft_id={draft.id} title={chinese_title[:40]}...")
            if args.save_files:
                op = output_root / f"collection_{draft.id}.md"
                op.parent.mkdir(parents=True, exist_ok=True)
                op.write_text(content, encoding="utf-8")
                print(f"Saved to: {op}")
            if args.word:
                img_groups = []
                for ps in paper_summaries:
                    imgs = _resolve_images_for_id(ps["id"], args.workspace)
                    img_groups.append((ps["title"], imgs))
                op = output_root / f"collection_{draft.id}.docx"
                markdown_to_docx(content, img_groups, op)
                print(f"Saved to: {op}")
                # Copy image folders for each paper
                out_dir = op.parent
                for ps in paper_summaries:
                    _copy_image_folder(ps["id"], ps["title"], out_dir, args.workspace)
                print(f"Image folders saved to: {out_dir}")
            return

        # ── Single-paper generation mode ──
        # 统计
        success = 0
        failed = 0

        for paper in papers:
            current = papers.index(paper) + 1
            print(f"\n[{current}/{len(papers)}] paper_id={paper.id}: {paper.title[:50]}...")

            try:
                generate_for_paper(
                    paper=paper,
                    session=session,
                    llm=llm,
                    model_name=model_name,
                    prompts_cfg=prompts_cfg,
                    themes=themes,
                    output_dir=output_dir,
                    output_root=output_root,
                    workspace=args.workspace,
                    profile=args.profile,
                    profile_data=profile_data,
                    dry_run=args.dry_run,
                    word_output=args.word,
                    get_model_config=_get_model_config,
                    draft_session=session,
                )
                success += 1
            except Exception as e:
                failed += 1
                print(f"ERROR: Failed to generate for paper {paper.id}: {e}")
                if not args.dry_run:
                    session.rollback()
                continue

            # Get batch_size with default for backward compatibility with mocked tests
            batch_size = 10
            if hasattr(args, 'batch_size') and isinstance(args.batch_size, (int, float)):
                batch_size = int(args.batch_size)
            if not args.dry_run and (success + failed) % batch_size == 0:
                session.commit()
                print(f"  [batch commit] {success + failed} done so far")

        elapsed = time.time() - start
        skipped = len(papers) - success - failed
        print("\n=== Summary ===")
        print(f"Total: {len(papers)}")
        print(f"Success: {success}")
        print(f"Failed: {failed}")
        if skipped > 0:
            print(f"Skipped: {skipped}")
        print(f"耗时: {elapsed:.1f}s")
        if success + failed > 0:
            print(f"平均: {elapsed/(success+failed):.1f}s/篇")
        if args.dry_run:
            print("Dry-run completed, no changes saved.")


if __name__ == "__main__":
    main()
