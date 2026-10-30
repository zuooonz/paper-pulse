#!/usr/bin/env python3
"""Command-line interface for PDF linking and extraction pipeline

Usage:
    python scripts/run_pdf_pipeline.py [--link] [--extract] [--dry-run] [--limit N] [--paper-id ID]

Examples:
    # Only link PDF paths (dry run)
    python scripts/run_pdf_pipeline.py --link --dry-run

    # Link and extract all PDFs
    python scripts/run_pdf_pipeline.py

    # Only extract, limit to 10 papers
    python scripts/run_pdf_pipeline.py --extract --limit 10

    # Process specific paper by internal_id
    python scripts/run_pdf_pipeline.py --extract --paper-id 123
"""

import argparse
from pathlib import Path

from sqlalchemy import select

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from paper_library.pdf_pipeline import link_pdfs, extract_paper
from tools.storage import get_pdf_dir, get_image_root


def main():
    parser = argparse.ArgumentParser(description="PDF linking and extraction pipeline")
    add_db_arguments(parser)
    parser.add_argument("--link", action="store_true", help="Only run PDF path linking")
    parser.add_argument("--extract", action="store_true", help="Only run PDF text extraction")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify database")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of papers to extract")
    parser.add_argument("--paper-id", type=int, default=None, help="Process specific paper by internal_id")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="PDF directory (defaults to storage config)")
    parser.add_argument("--image-output-dir", type=Path, default=None, help="Extracted images directory (defaults to storage config)")
    parser.add_argument("--workspace", default=".", help="Workspace root directory")
    parser.add_argument("--config-dir", default="config", help="Config directory")
    args = parser.parse_args()
    prepare_database(args, workspace=".")

    # Default: run both link and extract if neither is specified
    do_link = args.link or (not args.link and not args.extract)
    do_extract = args.extract or (not args.link and not args.extract)

    # Resolve paths relative to workspace
    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else get_pdf_dir(args.workspace)
    image_output_dir = Path(args.image_output_dir) if args.image_output_dir else get_image_root(args.workspace)
    if not pdf_dir.is_absolute():
        pdf_dir = Path(args.workspace) / pdf_dir
    if not image_output_dir.is_absolute():
        image_output_dir = Path(args.workspace) / image_output_dir

    print(f"Workspace: {args.workspace}")
    print(f"PDF directory: {pdf_dir}")
    print(f"Images output: {image_output_dir}")
    print()

    with get_session(args.workspace, args.config_dir) as session:
        # Step 1: Link PDFs
        if do_link:
            print("=== Step 1: Linking PDF paths ===")
            stats = link_pdfs(session, pdf_dir, dry_run=args.dry_run)
            print()
            print(f"Linking done. Linked: {stats['linked']}, Already set: {stats['already_set']}, No match: {stats['no_match']}")
            print()

        # Step 2: Extract text from PDFs
        if do_extract:
            print("=== Step 2: Extracting PDF text ===")
            image_output_dir.mkdir(parents=True, exist_ok=True)

            # Build query
            if args.paper_id is not None:
                # Specific paper by internal_id
                stmt = select(Paper).where(Paper.internal_id == args.paper_id)
            else:
                # Extract papers that have pdf_storage_path but not yet extracted
                stmt = (
                    select(Paper)
                    .where(Paper.pdf_storage_path.isnot(None))
                    .where(Paper.processing_status != "done")
                    .order_by(Paper.internal_id)
                )
                if args.limit:
                    stmt = stmt.limit(args.limit)

            papers = list(session.scalars(stmt).all())
            total = len(papers)
            print(f"Found {total} papers to process")
            print()

            success = 0
            skipped = 0
            failed = 0

            for i, paper in enumerate(papers, 1):
                internal_id = paper.internal_id
                paper_img_dir = image_output_dir / f"{paper.id}"
                print(f"[{i}/{total}] Processing internal_id={internal_id} ... ", end="", flush=True)
                result = extract_paper(session, paper, paper_img_dir, dry_run=args.dry_run)
                if result:
                    if not args.dry_run:
                        if paper.processing_status == "done":
                            print("[OK] done")
                            success += 1
                        else:
                            print(f"[FAIL] {paper.processing_status}")
                            failed += 1
                    else:
                        print("(dry run)")
                        success += 1
                else:
                    print("skipped (no PDF path or file not found)")
                    skipped += 1

            print()
            print(f"Extraction done. Success: {success}, Skipped: {skipped}, Failed: {failed}")
            print()

    print("Pipeline completed.")


if __name__ == "__main__":
    main()
