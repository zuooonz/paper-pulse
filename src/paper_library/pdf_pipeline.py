"""PDF path linking and text extraction pipeline

This module provides functions to:
1. Link PDF files in the configured PDF root to database papers by internal_id from filename
2. Extract full text and images from linked PDFs and store in database
"""

import re
from pathlib import Path
from typing import Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from paper_library.models import Paper
from paper_library.paper_lifecycle import (
    complete_processing, fail_processing, refresh_metadata_complete,
    refresh_pdf_flag, start_processing,
)
from core.pdf_extractor import extract_text_and_images
from tools.storage import get_pdf_dir


def _resolve_workspace_path(rel_or_abs: str, workspace: Path) -> Path:
    """Resolve a path that may be relative or absolute."""
    p = Path(rel_or_abs)
    if p.is_absolute():
        return p
    return (workspace / p).resolve()


def link_pdfs(session: Session, pdf_dir: Path | None = None, dry_run: bool = False, workspace: str = ".") -> Dict[str, int]:
    """Link PDF files to database papers by internal_id extracted from filename.

    Filename format: {paper_id}_{internal_id_or_0}_{year}_{snippet}.pdf
    e.g. "100_50_2023_article_title.pdf" -> internal_id=50 (paper_id=100)
    Papers with an unassigned internal ID are skipped.

    Args:
        session: SQLAlchemy database session
        pdf_dir: Directory containing PDF files to link
        dry_run: If True, only print what would be done without modifying DB
        workspace: Project root directory (for relative path resolution)

    Returns:
        Statistics dict: {"linked": int, "already_set": int, "no_match": int}
    """
    stats: Dict[str, int] = {"linked": 0, "already_set": 0, "no_match": 0}
    ws = Path(workspace).resolve()
    pdf_dir = Path(pdf_dir) if pdf_dir is not None else get_pdf_dir(workspace)

    if not pdf_dir.exists():
        print(f"PDF directory {pdf_dir} does not exist")
        return stats

    # Iterate over all PDF files
    for pdf_path in pdf_dir.glob("*.pdf"):
        filename = pdf_path.name

        # Extract internal_id from filename
        # Format: {internal_id:04d}_{paper_id}_{year}_{snippet}.pdf
        match = re.match(r"^(\d{4})_(\d+)_(\d{4})_", filename)
        if not match:
            stats["no_match"] += 1
            continue

        try:
            internal_id = int(match.group(1))
        except ValueError:
            stats["no_match"] += 1
            continue

        if internal_id == 9999:
            # Unassigned paper — skip; these are managed by the download pipeline.
            stats["no_match"] += 1
            continue

        # Find paper by internal_id
        stmt = select(Paper).where(Paper.internal_id == internal_id)
        paper = session.scalar(stmt)

        if paper is None:
            stats["no_match"] += 1
            continue

        if paper.pdf_storage_path is not None:
            stats["already_set"] += 1
            continue

        # Link this PDF (store relative path for cross-platform compatibility)
        if pdf_path.is_absolute():
            try:
                rel_path = str(pdf_path.relative_to(ws)).replace("\\", "/")
            except ValueError:
                rel_path = str(pdf_path).replace("\\", "/")
        else:
            rel_path = str(pdf_path).replace("\\", "/")
        if dry_run:
            print(f"[DRY RUN] Would link internal_id={internal_id} -> {rel_path}")
        else:
            paper.pdf_storage_path = rel_path
            refresh_pdf_flag(paper, ws)
            print(f"Linked internal_id={internal_id} -> {rel_path}")
        stats["linked"] += 1

    if not dry_run:
        session.commit()

    return stats


def extract_paper(
    session: Session,
    paper: Paper,
    image_output_dir: Path,
    dry_run: bool = False,
    workspace: str = "."
) -> bool:
    """Extract text and images from a linked PDF and store in database.

    Args:
        session: SQLAlchemy database session
        paper: Paper object to process
        image_output_dir: Directory to save extracted images
        dry_run: If True, only print what would be done without modifying DB
        workspace: Project root directory (for relative path resolution)

    Returns:
        True if processing was attempted (successful or not), False if skipped
    """
    if not paper.pdf_storage_path:
        return False

    ws = Path(workspace).resolve()
    pdf_path = _resolve_workspace_path(paper.pdf_storage_path, ws)
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path} (db_path={paper.pdf_storage_path}, internal_id={paper.internal_id})")
        return False

    if dry_run:
        print(f"[DRY RUN] Would extract from {pdf_path} for internal_id={paper.internal_id}")
        return True

    # Extract text and images
    text, image_paths = extract_text_and_images(str(pdf_path), image_output_dir)

    if text:
        # Success
        start_processing(paper)
        paper.extracted_text = text
        paper.extracted_images = [{"path": p} for p in image_paths]
        paper.pdf_text_extracted = True
        complete_processing(paper)
    else:
        # Extraction failed
        paper.pdf_text_extracted = False
        fail_processing(paper, "pdf_empty")

    refresh_pdf_flag(paper, workspace)
    session.flush()
    session.expire(paper, ["authors"])
    refresh_metadata_complete(paper)

    session.commit()
    return True
