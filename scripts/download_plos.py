"""Download PLOS One PDFs for savedrecs papers missing full text."""
import sys, requests, time
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from core.net_safety import safe_get
from paper_library.models import Paper
from paper_library.paper_lifecycle import (
    complete_processing, fail_processing, record_download_error,
    refresh_metadata_complete, refresh_pdf_flag, start_processing,
)
from core.pdf_extractor import extract_text_and_images
from core.date_extractor import enrich_paper_dates
from core.country_extractor import enrich_paper_country
from core.entity_detector import detect_entity_with_paper
from tools.storage import get_pdf_dir, get_paper_image_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    with get_session(str(ROOT)) as session:
        papers = session.query(Paper).filter(
            Paper.source == 'savedrecs (1-1052)',
            Paper.extracted_text.is_(None),
            Paper.journal_name.ilike('%plos%'),
        ).all()

        print(f'PLOS papers to download: {len(papers)}')

        ok = 0
        for i, p in enumerate(papers, 1):
            doi = p.doi
            url = f'https://journals.plos.org/plosone/article/file?id={doi}&type=printable'

            try:
                resp = safe_get(requests, url, timeout=30)
                if resp.status_code != 200 or len(resp.content) < 10000:
                    record_download_error(p)
                    print(f'  [{i}/{len(papers)}] ID={p.id} DOWNLOAD FAILED (status={resp.status_code})')
                    continue

                pdf_dir = get_pdf_dir(ROOT)
                pdf_dir.mkdir(parents=True, exist_ok=True)
                dest = pdf_dir / f'9999_{p.id}_plos.pdf'
                dest.write_bytes(resp.content)
                p.pdf_storage_path = str(dest.resolve()).replace(chr(92), '/')
                start_processing(p)
                refresh_pdf_flag(p, ROOT)

                text, images = extract_text_and_images(str(dest), get_paper_image_dir(p.id, ROOT))
                if not text:
                    fail_processing(p, "pdf_empty")
                    print(f'  [{i}/{len(papers)}] ID={p.id} TEXT EXTRACTION FAILED')
                    continue

                p.extracted_text = text
                p.extracted_images = images
                p.pdf_text_extracted = True
                complete_processing(p)

                enrich_paper_dates(p)
                if p.published_date and not p.publication_month:
                    p.publication_month = p.published_date.month
                    p.publication_month_source = 'pdf'

                c, s = enrich_paper_country(p)
                if c and not p.corresponding_country:
                    p.corresponding_country = c
                    p.corresponding_country_source = s

                detected_entity, conf, _ = detect_entity_with_paper(p)
                if detected_entity:
                    p.entity_label = detected_entity
                    p.entity_source = 'fulltext_keyword'
                    p.entity_confidence = conf

                session.flush()
                session.expire(p, ["authors"])
                refresh_metadata_complete(p)

                ok += 1
                print(f'  [{i}/{len(papers)}] ID={p.id} OK ({len(text)} chars, entity={detected_entity})')

                if i % 5 == 0:
                    session.commit()

                time.sleep(0.5)

            except Exception as e:
                print(f'  [{i}/{len(papers)}] ID={p.id} ERROR: {e}')

        session.commit()
        print(f'\nDone: {ok}/{len(papers)} downloaded')


if __name__ == '__main__':
    main()
