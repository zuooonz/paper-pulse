"""将论文库内容导出为适合知识库和 RAG 检索的 Markdown 文档。"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from paper_library.models import Paper, paper_authors as pa_tbl


def _get_lead_authors(paper: Paper) -> tuple[list[dict], list[dict]]:
    """从 paper_authors 表获取第一/通讯作者列表（dict 格式）。"""
    first: list[dict] = []
    corr: list[dict] = []
    if not paper.authors:
        return first, corr
    # 从已加载的 paper.authors 关系中筛选
    author_map = {a.id: a for a in paper.authors}
    # 需要查询 paper_authors 关联表获取 is_first / is_corresponding
    from sqlalchemy import inspect
    insp = inspect(paper)
    if not insp.session:
        return first, corr
    rows = insp.session.execute(
        __import__('sqlalchemy').select(pa_tbl.c.author_id, pa_tbl.c.is_first, pa_tbl.c.is_corresponding)
        .where(pa_tbl.c.paper_id == paper.id)
    ).fetchall()
    for row in rows:
        a = author_map.get(row.author_id)
        if not a:
            continue
        d = {"name": a.name or "", "name_zh": a.name_zh or "", "affiliation": a.affiliation or ""}
        if row.is_first:
            first.append(d)
        if row.is_corresponding:
            corr.append(d)
    return first, corr


@dataclass
class ExportStats:
    total: int = 0
    exported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    total_bytes: int = 0


def _slugify(text: str, max_len: int = 60) -> str:
    """将标题转为文件名安全的 slug"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len]


def _strip_jats_xml(text: str) -> str:
    """清理 JATS XML 标签，提取纯文本"""
    if not text or "<jats:" not in text:
        return text
    # 将 <jats:title> 转为段落标题
    text = re.sub(r"\s*<jats:title>(.*?)</jats:title>\s*", r"\n\n**\1**\n", text)
    # <jats:p> 转为段落分隔
    text = re.sub(r"\s*<jats:p>\s*", "\n", text)
    text = re.sub(r"\s*</jats:p>\s*", "\n", text)
    # 移除所有其他 XML 标签
    text = re.sub(r"<[^>]+>", "", text)
    # 清理每行首尾空白
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    # 合并多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_list(val) -> list:
    """将 JSON 字段安全转为 list"""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _get_impact_factor(paper: Paper) -> Optional[float]:
    """从期刊指标获取最新 IF"""
    if not paper.journal:
        return None
    metrics = sorted(
        [m for m in paper.journal.metrics if m.impact_factor],
        key=lambda m: m.year or 0,
        reverse=True,
    )
    return metrics[0].impact_factor if metrics else None


def _get_jcr_quartile(paper: Paper) -> Optional[str]:
    """从期刊指标获取最新 JCR 分区"""
    if not paper.journal:
        return None
    metrics = sorted(
        [m for m in paper.journal.metrics if m.jcr_quartile],
        key=lambda m: m.year or 0,
        reverse=True,
    )
    return metrics[0].jcr_quartile if metrics else None


def _get_cas_quartile(paper: Paper) -> Optional[str]:
    """从期刊指标获取最新中科院分区"""
    if not paper.journal:
        return None
    metrics = sorted(
        [m for m in paper.journal.metrics if m.cas_quartile],
        key=lambda m: m.year or 0,
        reverse=True,
    )
    return metrics[0].cas_quartile if metrics else None


def _get_emerging_quartile(paper: Paper) -> Optional[str]:
    """从期刊指标获取最新新锐分区"""
    if not paper.journal:
        return None
    metrics = sorted(
        [m for m in paper.journal.metrics if m.emerging_quartile],
        key=lambda m: m.year or 0,
        reverse=True,
    )
    return metrics[0].emerging_quartile if metrics else None


def _format_pmid(pmid) -> str:
    """格式化 PMID（去掉浮点 .0）"""
    if not pmid:
        return ""
    s = str(pmid).strip()
    if s.replace(".", "").isdigit():
        return str(int(float(s)))
    return s


def _format_author_list(author_json) -> list[str]:
    """从 JSON 作者字段提取姓名列表"""
    authors = _safe_list(author_json)
    names = []
    for a in authors:
        if isinstance(a, dict):
            name = a.get("name", "")
            if name:
                names.append(name)
        elif isinstance(a, str):
            names.append(a)
    return names


def export_paper_to_markdown(paper: Paper, include_full_text: bool = False) -> str:
    """将单篇论文转为 Markdown 字符串

    结构设计面向知识库和 RAG 检索场景：
    - 引用节：用户问"怎么引用"时命中
    - 作者节：用户问"谁做的""哪个单位"时命中
    - 设备节：用户问"用的什么设备""多少通道"时命中
    - 摘要/结论/要点：用户问"研究什么""发现什么"时命中

    Args:
        paper: Paper ORM 对象（需 eager load journal, authors）
        include_full_text: 是否包含全文

    Returns:
        Markdown 格式的论文文档
    """
    lines: list[str] = []

    # ── 标题 ──
    title = paper.title or "(无标题)"
    lines.append(f"# {title}")
    if paper.title_zh:
        lines.append(f"\n**中文标题**: {paper.title_zh}")

    # ── 引用 ──
    if paper.apa_citation:
        lines.append("\n## 引用")
        lines.append(paper.apa_citation.strip())

    # ── 基本信息 ──
    lines.append("\n## 基本信息")

    if paper.internal_id:
        lines.append(f"- **论文ID**: {paper.internal_id}")
    if paper.doi:
        lines.append(f"- **DOI**: [{paper.doi}](https://doi.org/{paper.doi})")

    pmid_str = _format_pmid(paper.pmid)
    if pmid_str:
        pmid_line = f"- **PMID**: {pmid_str}"
        if paper.pubmed_url:
            pmid_line += f" ([PubMed]({paper.pubmed_url}))"
        lines.append(pmid_line)

    # 期刊（含卷期页）
    if paper.journal_name:
        j_name = html.unescape(paper.journal_name)
        journal_line = f"- **期刊**: {j_name}"
        if paper.year:
            journal_line += f" ({paper.year})"
        vol_parts = []
        if paper.volume:
            vol_parts.append(f"Vol.{paper.volume}")
        if paper.issue:
            vol_parts.append(f"Issue {paper.issue}")
        if paper.pages:
            vol_parts.append(f"pp.{paper.pages}")
        if vol_parts:
            journal_line += ", " + ", ".join(vol_parts)
        lines.append(journal_line)

    # 影响因子和分区
    impact_factor = _get_impact_factor(paper)
    jcr_q = _get_jcr_quartile(paper)
    cas_q = _get_cas_quartile(paper)
    if impact_factor or jcr_q or cas_q:
        if_line = "- **期刊分区**: "
        parts = []
        if impact_factor:
            parts.append(f"IF {impact_factor:.2f}")
        if jcr_q:
            parts.append(f"JCR {jcr_q}")
        if cas_q:
            parts.append(f"中科院 {cas_q}")
        if_line += " / ".join(parts)
        lines.append(if_line)

    # 发表日期
    if paper.published_date:
        lines.append(f"- **发表日期**: {paper.published_date}")

    # ── 作者 ──
    all_authors = [a.name for a in paper.authors if a.name] if paper.authors else []
    first_auths, corr_auths = _get_lead_authors(paper)

    if first_auths or corr_auths or all_authors or paper.corresponding_region:
        lines.append("\n## 作者")
        if corr_auths:
            ca_parts = []
            for a in corr_auths:
                name = a.get("name", "")
                aff = a.get("affiliation", "")
                if name and aff:
                    ca_parts.append(f"{name} ({aff})")
                elif name:
                    ca_parts.append(name)
            if ca_parts:
                lines.append(f"- **通讯作者**: {'; '.join(ca_parts)}")

        if first_auths:
            fa_parts = []
            for a in first_auths:
                if isinstance(a, dict):
                    name = a.get("name", "")
                    aff = a.get("affiliation", "")
                    role = a.get("role", "")
                    if name and aff:
                        label = f"{name} ({aff})"
                    elif name:
                        label = name
                    else:
                        continue
                    if role and role != "first":
                        label += f" [{role}]"
                    fa_parts.append(label)
                elif isinstance(a, str):
                    fa_parts.append(a)
            if fa_parts:
                lines.append(f"- **第一作者**: {'; '.join(fa_parts)}")

        if all_authors:
            lines.append(f"- **全部作者**: {', '.join(all_authors)}")

        if paper.corresponding_region or paper.corresponding_country:
            region_parts = []
            if paper.corresponding_region:
                region_parts.append(str(paper.corresponding_region))
            if paper.corresponding_country:
                region_parts.append(str(paper.corresponding_country))
            lines.append(f"- **通讯所在地区**: {' / '.join(region_parts)}")

    # ── 研究分类 ──
    lines.append("\n## 研究分类")

    research_fields = _safe_list(paper.research_fields)
    if research_fields:
        lines.append(f"- **研究领域**: {', '.join(str(f) for f in research_fields)}")

    paradigm = _safe_list(paper.paradigm)
    if paradigm:
        lines.append(f"- **实验范式**: {', '.join(str(p) for p in paradigm)}")

    brain_regions = _safe_list(paper.brain_regions)
    if brain_regions:
        lines.append(f"- **研究脑区**: {', '.join(brain_regions)}")

    if paper.sample_size:
        lines.append(f"- **样本量**: {paper.sample_size}")

    # ── 设备 ──
    lines.append("\n## 设备")

    if paper.entity_label:
        entity_line = f"- **关联课题**: {paper.entity_label}"
        if paper.entity_confidence:
            entity_line += f" ({paper.entity_confidence})"
        lines.append(entity_line)

    equipment = _safe_list(paper.equipment_brand)
    if equipment:
        lines.append(f"- **设备型号**: {', '.join(str(e) for e in equipment)}")

    if paper.channel_count:
        lines.append(f"- **通道数**: {paper.channel_count}")

    if paper.uses_target_software is not None:
        lines.append(f"- **应用目标软件**: {'是' if paper.uses_target_software else '否'}")

    # 设备引用原文（含型号、采样率、波长等技术参数）
    if paper.device_citation_text:
        lines.append(f"- **设备描述原文**: {paper.device_citation_text.strip()}")

    # ── 摘要 ──
    if paper.abstract:
        lines.append("\n## 摘要")
        lines.append(_strip_jats_xml(paper.abstract.strip()))

    # ── 研究结论 ──
    if paper.conclusions_zh:
        lines.append("\n## 研究结论")
        lines.append(paper.conclusions_zh.strip())

    # ── 研究要点 ──
    highlights = _safe_list(paper.highlights_zh)
    if highlights:
        lines.append("\n## 研究要点")
        for h in highlights:
            lines.append(f"- {h}")

    # ── 关键词 ──
    keywords = _safe_list(paper.keywords)
    keywords_zh = _safe_list(paper.keywords_zh)
    if keywords or keywords_zh:
        kw_parts = []
        if keywords:
            kw_parts.extend(str(k) for k in keywords)
        if keywords_zh:
            kw_parts.extend(str(k) for k in keywords_zh)
        lines.append(f"\n**关键词**: {', '.join(kw_parts)}")

    # ── 全文（可选）──
    if include_full_text and paper.extracted_text:
        lines.append("\n## 全文")
        text = paper.extracted_text.strip()
        if len(text) > 10000:
            text = text[:10000] + "\n\n...(全文截断)"
        lines.append(text)

    # ── 来源标识（每篇末尾，确保任意 chunk 都包含出处）──
    j_name = html.unescape(paper.journal_name) if paper.journal_name else "未知期刊"
    year_str = str(paper.year) if paper.year else "未知年份"
    lines.append("")
    lines.append("---")
    lines.append(f"来源: {title}")
    if paper.doi:
        lines.append(f"DOI: {paper.doi}")
    lines.append(f"出处: {j_name} ({year_str})")
    lines.append("")


def export_paper_to_markdown_chunked(paper: Paper) -> str:
    """单篇论文 → chunk-optimized Markdown（3个##段 + 唯一分隔符）

    结构:
        # Title
        ## 论文信息
        ## 摘要
        ## 研究发现
        <!-- PAPER:N END -->
    """
    title = paper.title or "(无标题)"
    lines = [f"# {title}"]

    if paper.title_zh:
        lines.append(f"\n**中文标题**: {paper.title_zh}")

    # ═══ ## 论文信息 ═══
    lines.append("\n## 论文信息")

    # ID + DOI + PMID
    id_parts = []
    if paper.internal_id:
        id_parts.append(f"ID:{paper.internal_id}")
    if paper.doi:
        id_parts.append(f"DOI:{paper.doi}")
    pmid_str = _format_pmid(paper.pmid)
    if pmid_str:
        id_parts.append(f"PMID:{pmid_str}")
    if id_parts:
        lines.append(f"- **标识**: {' | '.join(id_parts)}")

    # 期刊
    if paper.journal_name:
        j_name = html.unescape(paper.journal_name)
        jl = f"- **期刊**: {j_name} ({paper.year or '?'})"
        vol_parts = []
        if paper.volume:
            vol_parts.append(f"Vol.{paper.volume}")
        if paper.pages:
            vol_parts.append(f"pp.{paper.pages}")
        if vol_parts:
            jl += ", " + ", ".join(vol_parts)
        impact_factor = _get_impact_factor(paper)
        jcr_q = _get_jcr_quartile(paper)
        if_line = []
        if impact_factor:
            if_line.append(f"IF {impact_factor:.2f}")
        if jcr_q:
            if_line.append(f"JCR {jcr_q}")
        cas_q = _get_cas_quartile(paper)
        if cas_q:
            if_line.append(f"中科院 {cas_q}")
        if if_line:
            jl += " | " + " / ".join(if_line)
        if paper.published_date:
            jl += f" | 发表: {paper.published_date}"
        lines.append(jl)

    # 引用
    if paper.apa_citation:
        lines.append(f"- **引用**: {paper.apa_citation.strip()}")

    # 作者
    first_auths, corr_auths = _get_lead_authors(paper)
    if corr_auths:
        ca_parts = []
        for a in corr_auths:
            name = a.get("name", "")
            aff = a.get("affiliation", "")
            if name and aff:
                ca_parts.append(f"{name}({aff})")
            elif name:
                ca_parts.append(name)
        if ca_parts:
            lines.append(f"- **通讯作者**: {'; '.join(ca_parts)}")

    if first_auths:
        fa_parts = []
        for a in first_auths:
            if isinstance(a, dict):
                name = a.get("name", "")
                aff = a.get("affiliation", "")
                role = a.get("role", "")
                label = f"{name}({aff})" if aff else name
                if role and role != "first":
                    label += f"[{role}]"
                fa_parts.append(label)
            elif isinstance(a, str):
                fa_parts.append(a)
        if fa_parts:
            lines.append(f"- **第一作者**: {'; '.join(fa_parts)}")

    # 全部作者 (English)
    all_authors = [a.name for a in paper.authors if a.name] if paper.authors else []
    if all_authors:
        lines.append(f"- **全部作者**: {', '.join(all_authors)}")

    # 通讯地区
    if paper.corresponding_region or paper.corresponding_country:
        rp = [str(paper.corresponding_region)] if paper.corresponding_region else []
        if paper.corresponding_country:
            rp.append(str(paper.corresponding_country))
        lines.append(f"- **通讯地区**: {' / '.join(rp)}")

    # 研究领域 + 范式 + 脑区 (合并到论文信息)
    rf = _safe_list(paper.research_fields)
    pa = _safe_list(paper.paradigm)
    br = _safe_list(paper.brain_regions)
    if rf:
        lines.append(f"- **研究领域**: {', '.join(str(f) for f in rf)}")
    if pa:
        lines.append(f"- **实验范式**: {', '.join(str(p) for p in pa)}")
    if br:
        lines.append(f"- **研究脑区**: {', '.join(br)}")

    # 设备
    eq_parts = []
    if paper.entity_label:
        eq_parts.append(f"课题: {paper.entity_label}")
    equipment = _safe_list(paper.equipment_brand)
    if equipment:
        eq_parts.append(f"型号: {', '.join(str(e) for e in equipment)}")
    if paper.channel_count:
        eq_parts.append(f"通道: {paper.channel_count}")
    if paper.uses_target_software is not None:
        eq_parts.append(f"目标软件: {'是' if paper.uses_target_software else '否'}")
    if eq_parts:
        lines.append(f"- **设备**: {' | '.join(eq_parts)}")
    if paper.device_citation_text:
        lines.append(f"- **设备描述**: {paper.device_citation_text.strip()}")

    # ═══ ## 摘要 ═══
    if paper.abstract:
        lines.append("\n## 摘要")
        lines.append(_strip_jats_xml(paper.abstract.strip()))

    # ═══ ## 研究发现 ═══
    has_findings = False
    finding_lines = []
    if paper.conclusions_zh:
        finding_lines.append(f"**结论**: {paper.conclusions_zh.strip()}")
        has_findings = True
    highlights = _safe_list(paper.highlights_zh)
    if highlights:
        hp = '; '.join(str(h) for h in highlights)
        finding_lines.append(f"**要点**: {hp}")
        has_findings = True
    # Keywords
    kw_parts = []
    for k in _safe_list(paper.keywords):
        kw_parts.append(str(k))
    for k in _safe_list(paper.keywords_zh):
        kw_parts.append(str(k))
    if kw_parts:
        finding_lines.append(f"**关键词**: {', '.join(kw_parts)}")
        has_findings = True
    if has_findings:
        lines.append("\n## 研究发现")
        lines.extend(finding_lines)

    # ═══ Footer / separator ═══
    pid = paper.internal_id or paper.id
    lines.append("")
    lines.append(f"<!-- PAPER:{pid} END -->")
    lines.append("")

    return "\n".join(lines)

    return "\n".join(lines)


def export_papers_merged_chunked(
    output_path: Path,
    papers: list[Paper],
    dry_run: bool = False,
) -> ExportStats:
    """导出为 chunk-optimized 格式：3个##段 + 唯一分隔符"""
    stats = ExportStats(total=len(papers))
    sections = []

    for i, paper in enumerate(papers):
        try:
            if not paper.title:
                stats.skipped += 1
                continue
            md = export_paper_to_markdown_chunked(paper)
            sections.append(md)
            stats.exported += 1
            stats.total_bytes += len(md.encode("utf-8"))
            if (i + 1) % 100 == 0:
                print(f"  进度: {i + 1}/{len(papers)}", flush=True)
        except Exception as e:
            stats.errors.append(f"paper_id={paper.id}: {e}")
            stats.skipped += 1

    if not dry_run and sections:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged = "\n\n".join(sections)
        output_path.write_text(merged, encoding="utf-8")
        stats.total_bytes = output_path.stat().st_size

    return stats


def generate_filename(paper: Paper) -> str:
    """生成文件名: paper_{id}_{slug}.md"""
    slug = _slugify(paper.title or "untitled")
    if not slug:
        slug = "untitled"
    return f"paper_{paper.id:04d}_{slug}.md"


def export_papers_to_directory(
    session: Session,
    output_dir: Path,
    papers: list[Paper],
    include_full_text: bool = False,
    dry_run: bool = False,
) -> ExportStats:
    """批量导出论文到 Markdown 文件

    Args:
        session: 数据库 session
        output_dir: 输出目录
        papers: 要导出的论文列表
        include_full_text: 是否包含全文
        dry_run: 仅统计不写文件

    Returns:
        ExportStats 统计结果
    """
    stats = ExportStats(total=len(papers))

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for i, paper in enumerate(papers):
        try:
            if not paper.title:
                stats.skipped += 1
                continue

            md_content = export_paper_to_markdown(paper, include_full_text)
            filename = generate_filename(paper)
            filepath = output_dir / filename

            if not dry_run:
                filepath.write_text(md_content, encoding="utf-8")
                stats.total_bytes += filepath.stat().st_size
            else:
                stats.total_bytes += len(md_content.encode("utf-8"))

            stats.exported += 1

            if (i + 1) % 50 == 0:
                print(f"  进度: {i + 1}/{len(papers)}", flush=True)

        except Exception as e:
            stats.errors.append(f"paper_id={paper.id}: {e}")
            stats.skipped += 1

    return stats


def export_papers_merged(
    output_path: Path,
    papers: list[Paper],
    include_full_text: bool = False,
    dry_run: bool = False,
) -> ExportStats:
    """将所有论文合并为单个 Markdown 文件

    每篇论文以 # 一级标题开头，论文之间用 --- 分隔。
    知识库可按 # 标题切分文档。

    Args:
        output_path: 输出文件路径
        papers: 要导出的论文列表
        include_full_text: 是否包含全文
        dry_run: 仅统计不写文件

    Returns:
        ExportStats 统计结果
    """
    stats = ExportStats(total=len(papers))
    sections: list[str] = []

    for i, paper in enumerate(papers):
        try:
            if not paper.title:
                stats.skipped += 1
                continue

            md_content = export_paper_to_markdown(paper, include_full_text)
            sections.append(md_content)
            stats.exported += 1
            stats.total_bytes += len(md_content.encode("utf-8"))

            if (i + 1) % 100 == 0:
                print(f"  进度: {i + 1}/{len(papers)}", flush=True)

        except Exception as e:
            stats.errors.append(f"paper_id={paper.id}: {e}")
            stats.skipped += 1

    if not dry_run and sections:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        merged = "\n\n---\n\n".join(sections)
        output_path.write_text(merged, encoding="utf-8")
        stats.total_bytes = output_path.stat().st_size

    return stats


def load_papers_for_export(
    session: Session,
    internal_id_range: tuple[int, int] | None = None,
    paper_ids: list[int] | None = None,
    limit: int | None = None,
) -> list[Paper]:
    """加载论文数据（eager load 关联表）

    Args:
        session: 数据库 session
        internal_id_range: (min, max) internal_id 范围
        paper_ids: 指定的论文 ID 列表
        limit: 限制数量

    Returns:
        Paper 列表
    """
    query = (
        session.query(Paper)
        .options(
            joinedload(Paper.journal),
            joinedload(Paper.authors),
        )
        .filter(Paper.title.isnot(None))
    )

    if paper_ids:
        query = query.filter(Paper.id.in_(paper_ids))
    elif internal_id_range:
        lo, hi = internal_id_range
        query = query.filter(
            Paper.internal_id >= lo,
            Paper.internal_id <= hi,
        )

    query = query.order_by(Paper.id)

    if limit:
        query = query.limit(limit)

    return query.all()
