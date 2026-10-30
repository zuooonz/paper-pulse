# Auto-ingest pipeline: pending → done
import sys, time, argparse

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.cli import setup_console
from paper_library.models import Paper
from paper_library.paper_lifecycle import (
    complete_processing, fail_processing, refresh_metadata_complete,
    refresh_pdf_flag, record_download_error, start_processing,
)
from paper_library.author_ingest import normalize_authors_for_paper
from paper_library.doi import normalize_doi
from core.crossref_api import fetch_metadata_by_doi
from core.pdf_extractor import extract_text_and_images
from core.date_extractor import enrich_paper_dates
from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal
from paper_library.abstract_enrichment import (
    enrich_missing_abstract,
    extract_crossref_abstract,
    needs_chinese_abstract_package,
    translate_abstract_to_chinese,
)
from core.config_loader import ConfigLoader
from core.llm_client import OpenAICompatibleClient


def enrich_from_crossref(paper: Paper) -> str:
    """
    从 CrossRef 补全元数据。返回 'ok' / 'skip' / 'error:...'
    """
    if not paper.doi:
        return 'skip'
    doi = normalize_doi(paper.doi)
    if not doi:
        return f"error: invalid DOI: {paper.doi}"
    try:
        time.sleep(0.5)
        result = fetch_metadata_by_doi(doi, structured=True)
        if not result or result.get('_error') or result.get('error'):
            return f"error:{result.get('_error') or result.get('error','no result')}"
        raw = result.get('raw') or {}
        if not paper.abstract:
            paper.abstract = extract_crossref_abstract(result)
        if not paper.title or paper.title == '(无标题)':
            paper.title = result.get('title') or paper.title
        if not paper.year:
            paper.year = result.get('year')
        if not paper.journal_name:
            ct = raw.get('container-title') or []
            paper.journal_name = ct[0] if ct else None
        if not paper.volume:
            paper.volume = str(raw.get('volume', '')) or None
        if not paper.issue:
            paper.issue = str(raw.get('issue', '')) or None
        if not paper.pages:
            paper.pages = str(raw.get('page', '')) or None
        paper.doi_meta = raw or paper.doi_meta
        return 'ok'
    except Exception as e:
        return f"error:{e}"


def extract_pdf(paper: Paper) -> str:
    """
    提取 PDF 全文。返回 'ok' / 'skip' / 'error:...'
    """
    if not paper.pdf_storage_path:
        return 'skip'
    # Resolve path relative to workspace if relative
    pdf_path = Path(paper.pdf_storage_path)
    if not pdf_path.is_absolute():
        pdf_path = ROOT / paper.pdf_storage_path
    if not pdf_path.exists():
        return "skip_missing_pdf"
    if paper.pdf_text_extracted and paper.extracted_text:
        return 'skip'
    try:
        img_dir = ROOT / 'data' / 'images' / str(paper.id)
        text, images = extract_text_and_images(str(pdf_path), img_dir, max_images=6)
        paper.extracted_text = text
        paper.extracted_images = images or None
        paper.pdf_text_extracted = bool(text)
        return 'ok'
    except Exception as e:
        return f"error:{e}"


def main():
    setup_console()
    parser = argparse.ArgumentParser(description='Auto-ingest pipeline: pending → done')
    add_db_arguments(parser)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--paper-id', type=int, default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-crossref', action='store_true')
    parser.add_argument('--skip-pdf', action='store_true')
    parser.add_argument(
        '--translate-zh', action='store_true',
        help='显式启用中文摘要翻译（也可由 runtime.abstract_enrichment 启用）',
    )
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--config-dir', default='config')
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    stats = {'processed': 0, 'crossref_ok': 0, 'pdf_ok': 0,
             'abstract_ok': 0, 'translated_zh': 0,
             'dates_ok': 0, 'marked_done': 0, 'errors': 0}

    translation_client = None
    translation_model = "example-chat-model"
    try:
        config = ConfigLoader(Path(args.workspace).resolve() / args.config_dir).load_all()
        abstract_cfg = config.get('runtime', {}).get('abstract_enrichment', {})
        translation_enabled = args.translate_zh or (
            abstract_cfg.get('enabled', False) and abstract_cfg.get('translate_zh', False)
        )
        if translation_enabled:
            provider = config.get('runtime', {}).get('provider', {})
            model_sequence = config.get('models', {}).get('routing', {}).get('default_auto_sequence', [])
            translation_model = abstract_cfg.get('model') or (model_sequence[0] if model_sequence else translation_model)
            candidate = OpenAICompatibleClient(
                base_url=provider.get('base_url', ''),
                api_key=provider.get('api_key', ''),
                timeout_seconds=300,
                max_retries=3,
            )
            if candidate.available():
                translation_client = candidate
    except Exception as e:
        print(f"[WARN] abstract translation disabled: {e}")

    with get_session(args.workspace, args.config_dir) as session:
        stmt = select(Paper).where(Paper.processing_status == 'pending')
        if args.paper_id:
            stmt = stmt.where(Paper.id == args.paper_id)
        else:
            stmt = stmt.limit(args.limit)
        papers = list(session.scalars(stmt).all())
        print(f"Found {len(papers)} pending papers")

        for paper in papers:
            stats['processed'] += 1
            print(f"\n[{stats['processed']}] id={paper.id} doi={paper.doi}")
            paper_errors = []

            if args.dry_run:
                print(f"  [DRY] would process: crossref={not args.skip_crossref} pdf={not args.skip_pdf}")
                continue

            start_processing(paper)

            # Step 1: CrossRef
            if not args.skip_crossref:
                r = enrich_from_crossref(paper)
                print(f"  crossref: {r}")
                if r == 'ok':
                    stats['crossref_ok'] += 1
                elif r.startswith('error'):
                    # Crossref is an external best-effort source. Continue to
                    # cached metadata/full text instead of permanently marking
                    # an otherwise usable paper as failed.
                    print(f"  crossref-warning: {r}")
                session.flush()

            if paper.doi_meta:
                try:
                    with session.begin_nested():
                        normalize_authors_for_paper(paper.doi_meta, paper.id, session)
                except Exception as e:
                    print(f"  authors-warning: paper_id={paper.id}: {e}")

            # Step 2: Journal enrichment (IF, quartile) — added 2026-05
            try:
                matcher = JcrMatcher().load()
                result = enrich_paper_journal(paper, session, matcher)
                details = result.get('details', {})
                info_parts = []
                if 'impact_factor' in details:
                    info_parts.append(f"IF={details['impact_factor']}")
                if 'cas_quartile' in details:
                    info_parts.append(f"CAS={details['cas_quartile']}")
                if 'emerging_quartile' in details:
                    info_parts.append(f"XR={details['emerging_quartile']}")
                msg = ', '.join(info_parts) if info_parts else result['status']
                print(f"  journal: {result['status']} ({msg})")
                session.flush()
            except Exception as e:
                print(f"  journal: error:{e}")

            # Step 3: PDF extract
            if not args.skip_pdf:
                r = extract_pdf(paper)
                print(f"  pdf: {r}")
                if r == 'ok':
                    stats['pdf_ok'] += 1
                elif r == 'skip_missing_pdf':
                    record_download_error(paper, "pdf_file_missing")
                elif r.startswith('error'):
                    paper_errors.append(r)
                session.flush()

            # Step 4: deterministic abstract fallback, then optional translation.
            abstract_result = enrich_missing_abstract(paper, refresh_crossref=False)
            print(f"  abstract: {abstract_result.source}")
            if abstract_result.changed:
                stats['abstract_ok'] += 1
            if translation_client and paper.abstract and needs_chinese_abstract_package(paper):
                translated = translate_abstract_to_chinese(
                    paper, translation_client, model=translation_model,
                )
                print(f"  abstract-zh: {translated.source}")
                if translated.changed:
                    stats['translated_zh'] += 1
            session.flush()

            # Step 5: Extract accepted/published dates
            try:
                dates = enrich_paper_dates(paper)
                for field, (d, src) in dates.items():
                    label = "acc" if field == "accepted" else "pub"
                    status = d.isoformat() if d else "N/A"
                    print(f"  dates-{label}: {src or 'MISSING'} | {status}")
                session.flush()
            except Exception as e:
                print(f"  dates: error:{e}")

            # Step 6: mark status
            if paper_errors:
                fail_processing(paper, '; '.join(paper_errors))
                stats['errors'] += 1
                print(f"  → ERROR: {paper.processing_error}")
            else:
                complete_processing(paper)
                stats['marked_done'] += 1
                print("  → DONE")
            refresh_pdf_flag(paper, args.workspace)
            refresh_metadata_complete(paper)

            session.commit()

    print("\n=== Summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == '__main__':
    main()
