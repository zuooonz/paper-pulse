"""Scan a directory for PDFs, match to DB papers by DOI/text, ingest with proper naming."""
import logging

import sys, shutil, re
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import Paper
from paper_library.paper_lifecycle import (
    complete_processing, fail_processing, refresh_metadata_complete,
    refresh_pdf_flag, start_processing,
)
from paper_library.pdf_import import make_pdf_filename, extract_pdf_text
from core.pdf_extractor import extract_text_and_images
from core.date_extractor import enrich_paper_dates
from core.country_extractor import enrich_paper_country
from core.entity_detector import detect_entity_with_paper


def extract_doi_from_text(text, max_chars=5000):
    """Extract DOI from PDF text."""
    doi_pattern = r'\b10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+\b'
    matches = re.findall(doi_pattern, text[:max_chars])
    if matches:
        # Return longest match (DOI in PDF first page is usually the article DOI)
        return max(matches, key=len).rstrip('.').rstrip(',')
    return None


def match_paper(session, pdf_path):
    """Try to match a PDF to a DB paper. Returns Paper or None."""
    # Strategy 1: Extract DOI from PDF text
    text = extract_pdf_text(pdf_path, max_chars=5000)
    if text:
        doi = extract_doi_from_text(text)
        if doi:
            from paper_library.doi import normalize_doi
            nd = normalize_doi(doi)
            if nd:
                p = session.query(Paper).filter(Paper.doi == nd).first()
                if p:
                    return p, 'doi_in_pdf'

    # Strategy 2: Fuzzy title match (for papers already in DB without DOI match)
    if text:
        from paper_library.pdf_import import extract_candidate_title_from_pdf, title_similarity
        candidates = extract_candidate_title_from_pdf(text)
        if candidates:
            for cand in candidates[:5]:
                # Search DB for similar titles
                papers = session.query(Paper).filter(Paper.title.ilike(f'%{cand[:50]}%')).limit(5).all()
                for p in papers:
                    from paper_library.pdf_import import title_similarity
                    if title_similarity(p.title, cand) >= 0.7:
                        return p, 'title_match'
                # Try broader search
                words = cand.split()[:8]
                if len(words) >= 3:
                    pattern = '%' + '%'.join(words[:5]) + '%'
                    p = session.query(Paper).filter(Paper.title.ilike(pattern)).first()
                    if p:
                        return p, 'title_keywords'

    return None, None


def ingest_one(session, pdf_path, paper, match_method):
    """Ingest a single PDF: copy with correct name, extract, enrich."""
    pid = paper.id

    # If paper already has PDF, skip
    if paper.extracted_text and paper.pdf_storage_path:
        existing = Path(str(paper.pdf_storage_path).replace(chr(92), '/'))
        if existing.exists():
            return False, 'already_has_pdf'

    # Generate correct filename
    correct_name = make_pdf_filename(paper.id, paper.internal_id, paper.year, paper.title or 'untitled')
    dest = ROOT / 'data' / 'pdf' / correct_name

    # Handle duplicates
    if dest.exists():
        dest = ROOT / 'data' / 'pdf' / correct_name.replace('.pdf', f'_{int(__import__("time").time())}.pdf')

    shutil.copy2(pdf_path, dest)
    paper.pdf_storage_path = str(dest.resolve()).replace(chr(92), '/')
    start_processing(paper)
    refresh_pdf_flag(paper, ROOT)

    # Extract text and images
    img_dir = ROOT / 'data' / 'images' / str(pid)
    text, images = extract_text_and_images(str(dest), img_dir)
    if not text:
        fail_processing(paper, "pdf_empty")
        return False, 'text_extraction_failed'

    paper.extracted_text = text
    paper.extracted_images = images
    paper.pdf_text_extracted = True
    complete_processing(paper)

    # Enrich
    enrich_paper_dates(paper)
    if paper.published_date and not paper.publication_month:
        paper.publication_month = paper.published_date.month
        paper.publication_month_source = 'pdf'

    c, s = enrich_paper_country(paper)
    if c and not paper.corresponding_country:
        paper.corresponding_country = c
        paper.corresponding_country_source = s

    detected_entity, conf, _ = detect_entity_with_paper(paper)
    if detected_entity:
        paper.entity_label = detected_entity
        paper.entity_source = 'fulltext_keyword'
        paper.entity_confidence = conf

    session.flush()
    session.expire(paper, ["authors"])
    refresh_metadata_complete(paper)

    return True, f'{len(text)} chars' + (f' entity={detected_entity}' if detected_entity else '')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Batch ingest downloaded PDFs')
    add_db_arguments(parser)
    parser.add_argument('--dir', default=str(Path.home() / 'Downloads' / 'pdf'),
                        help='Directory containing PDF files')
    parser.add_argument('--move-done', action='store_true',
                        help='Move processed PDFs to _done/ subdirectory')
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    pdf_dir = Path(args.dir)
    if not pdf_dir.exists():
        print(f'Directory not found: {pdf_dir}')
        print('Create it and place PDF files there.')
        return

    pdf_files = list(pdf_dir.glob('*.pdf'))
    if not pdf_files:
        print(f'No PDF files found in {pdf_dir}')
        return

    print(f'Found {len(pdf_files)} PDF files')
    done_dir = pdf_dir / '_done'
    if args.move_done:
        done_dir.mkdir(exist_ok=True)

    ok = fail = skip = 0

    with get_session(str(ROOT)) as session:
        for pdf_path in pdf_files:
            print(f'\n{pdf_path.name} ({pdf_path.stat().st_size:,d} bytes)')

            # Match
            paper, method = match_paper(session, pdf_path)
            if not paper:
                print('  [NO MATCH] Could not find paper in DB')
                fail += 1
                continue

            print(f'  Matched: ID={paper.id} via {method}')
            print(f"  Title: {(paper.title or '?')[:70]}")

            # Check if already has PDF
            if paper.pdf_storage_path:
                existing = Path(str(paper.pdf_storage_path).replace(chr(92), '/'))
                if existing.exists():
                    print(f'  [SKIP] Already has PDF: {existing.name}')
                    skip += 1
                    if args.move_done:
                        shutil.move(str(pdf_path), str(done_dir / pdf_path.name))
                    continue

            # Ingest
            success, msg = ingest_one(session, pdf_path, paper, method)
            if success:
                ok += 1
                print(f'  [OK] {msg}')
            else:
                fail += 1
                print(f'  [FAIL] {msg}')

            if args.move_done:
                try:
                    shutil.move(str(pdf_path), str(done_dir / pdf_path.name))
                except Exception:
                    logger.warning('处理失败，已跳过该项（main）', exc_info=True)
                    pass

        session.commit()

    print(f'\n=== Done: {ok} OK, {fail} failed, {skip} skipped ===')
    # Final stats
    with get_session(str(ROOT)) as session:
        total = session.query(Paper).count()
        pdf = session.query(Paper).filter(Paper.extracted_text.isnot(None)).count()
        print(f'PDF coverage: {pdf}/{total} ({pdf/total*100:.1f}%)')


if __name__ == '__main__':
    main()
