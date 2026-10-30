#!/usr/bin/env python3
"""Seed the curated public demo library snapshot.

The snapshot contains only selected metadata and author names from the local
demo library; PDFs and extracted full text are not committed. Running this
script creates synthetic PDF placeholders for rows that had an attachment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database, setup_console  # noqa: E402
from paper_library.db import get_session  # noqa: E402
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal  # noqa: E402
from paper_library.models import Author, Paper, PaperAuthorMention, paper_authors  # noqa: E402
from paper_library.paper_lifecycle import compute_metadata_complete, refresh_pdf_flag  # noqa: E402
from sqlalchemy import select  # noqa: E402

from scripts.seed_demo_data import _seed_drafts, _write_demo_pdf, reset_demo_data  # noqa: E402

SOURCE = "demo_seed"
DEFAULT_FIXTURE = ROOT / "scripts" / "demo_library.json"
_DATE_FIELDS = ("accepted_date", "published_date")


def _demo_title(value: str | None) -> str:
    title = (value or "").strip()
    if title.lower().startswith("[demo]"):
        title = title[6:].strip()
    return f"[demo] {title}" if title else "[demo] Untitled demo paper"


def _paper_fields(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {column.name for column in Paper.__table__.columns}
    fields = {
        key: value
        for key, value in item.items()
        if key in allowed and key not in {
            "id", "created_at", "updated_at", "pdf_storage_path", "pdf_text_extracted",
            "extracted_images", "extracted_text", "reviewed_by", "reviewed_at",
            "processing_error", "source",
        }
    }
    fields["title"] = _demo_title(fields.get("title"))
    if fields.get("title_zh"):
        fields["title_zh"] = _demo_title(fields["title_zh"])
    for key in _DATE_FIELDS:
        value = fields.get(key)
        if isinstance(value, str) and value:
            try:
                fields[key] = dt.date.fromisoformat(value)
            except ValueError:
                fields.pop(key, None)
    fields["source"] = SOURCE
    fields["original_data"] = {"source": SOURCE, "demo": True}
    return fields


def _ensure_author(session, data: dict[str, Any]) -> Author:
    query = select(Author).where(Author.name == (data.get("name") or ""))
    if data.get("name_zh"):
        query = query.where(Author.name_zh == data["name_zh"])
    if data.get("affiliation_zh"):
        query = query.where(Author.affiliation_zh == data["affiliation_zh"])
    author = session.scalar(query.limit(1))
    if author is not None:
        return author
    author = Author(
        name=data.get("name") or "Demo Author",
        name_zh=data.get("name_zh"),
        orcid=data.get("orcid"),
        email=data.get("email"),
        affiliation=data.get("affiliation"),
        affiliation_zh=data.get("affiliation_zh"),
        country=data.get("country"),
        province=data.get("province"),
        city=data.get("city"),
        institution_type=data.get("institution_type"),
        department=data.get("department"),
    )
    session.add(author)
    session.flush()
    return author


def _attach_authors(session, paper: Paper, authors: list[dict[str, Any]]) -> None:
    for index, data in enumerate(authors):
        author = _ensure_author(session, data)
        order = int(data.get("order") or index)
        is_first = bool(data.get("is_first")) or index == 0
        is_corresponding = bool(data.get("is_corresponding")) or index == len(authors) - 1
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=author.id,
            author_order=order,
            is_first=is_first,
            is_corresponding=is_corresponding,
        ))
        session.add(PaperAuthorMention(
            paper_id=paper.id,
            author_id=author.id,
            name_raw=author.name,
            name_zh=author.name_zh,
            affiliation_raw=author.affiliation,
            affiliation_zh=author.affiliation_zh,
            orcid_raw=author.orcid,
            source_type=SOURCE,
            source_ref=paper.doi,
            verification_status="unverified",
            evidence_text="Public demo library metadata.",
        ))
        if is_corresponding:
            paper.corresponding_country = author.country
            paper.corresponding_province = author.province
            paper.corresponding_city = author.city
            paper.corresponding_region = author.city
            paper.corresponding_institution_type = author.institution_type


def seed_demo_library(
    session,
    *,
    matcher: JcrMatcher,
    workspace: str | Path = ROOT,
    fixture_path: str | Path = DEFAULT_FIXTURE,
) -> dict[str, int]:
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    items = fixture.get("papers") if isinstance(fixture, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError(f"demo fixture is empty or invalid: {fixture_path}")

    created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _demo_title(item.get("title"))
        existing = session.scalar(select(Paper).where(Paper.title == title).limit(1))
        if existing is not None:
            refresh_pdf_flag(existing, workspace)
            continue
        fields = _paper_fields({**item, "title": title})
        paper = Paper(**fields)
        session.add(paper)
        session.flush()
        enrich_paper_journal(paper, session, matcher)
        _attach_authors(session, paper, item.get("authors") or [])
        if paper.has_pdf:
            _write_demo_pdf(paper, workspace)
        if paper.review_outcome in {"approved", "excluded"}:
            paper.reviewed_by = "demo_reviewer"
            paper.reviewed_at = dt.datetime.now(dt.UTC)
        if paper.metadata_complete is None:
            paper.metadata_complete = compute_metadata_complete(paper)
        created += 1

    demo_papers = list(session.scalars(
        select(Paper).where(Paper.source == SOURCE).order_by(Paper.id)
    ).all())
    _seed_drafts(session, demo_papers)
    session.flush()
    return {"papers_created": created, "papers_total": len(demo_papers), "drafts": 4}


def main() -> None:
    setup_console()
    parser = argparse.ArgumentParser(description="写入公开演示论文库快照")
    add_db_arguments(parser)
    parser.add_argument("--reset", action="store_true", help="先删除本脚本创建的演示数据")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="演示库 JSON 快照路径")
    parser.add_argument("--workspace", default=str(ROOT), help="应用工作目录")
    parser.add_argument("--config-dir", default="config", help="配置目录")
    args = parser.parse_args()

    prepare_database(args, workspace=args.workspace, config_dir=args.config_dir)
    workspace = Path(args.workspace).resolve()
    config_dir = Path(args.config_dir)
    if not config_dir.is_absolute():
        config_dir = workspace / config_dir
    matcher = JcrMatcher(
        config_path=config_dir / "reference.yaml",
        workspace=workspace,
    ).load()
    with get_session(str(workspace), str(config_dir)) as session:
        if args.reset:
            stats = reset_demo_data(session, workspace)
            print(f"已清理演示数据：论文 {stats['papers_removed']} 篇，草稿 {stats['drafts_removed']} 篇。")
        result = seed_demo_library(
            session,
            matcher=matcher,
            workspace=workspace,
            fixture_path=args.fixture,
        )
    print(
        f"演示库已就绪：新增论文 {result['papers_created']} 篇，"
        f"演示论文共 {result['papers_total']} 篇，草稿 {result['drafts']} 篇。"
    )


if __name__ == "__main__":
    main()
