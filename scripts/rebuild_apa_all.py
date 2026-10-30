#!/usr/bin/env python3
"""Rebuild APA citations for target-entity papers, then re-export the library."""
import argparse
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.crud import _build_apa_for_paper_data
from tools.markdown_exporter import export_papers_merged, load_papers_for_export
from sqlalchemy import text
from core.entity_detector import get_target_label
from tools.storage import get_output_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    target_label = get_target_label()
    if not target_label:
        print("Target entity is not configured; nothing to process.")
        return
    with get_session(str(ROOT), "config") as session:
        # Step 1: Load target-entity papers with internal_id
        papers = session.execute(
            text("SELECT id, internal_id, title, year, journal_name, volume, pages, doi, first_authors, corresponding_authors, doi_meta FROM papers WHERE entity_label = :target_label AND internal_id IS NOT NULL ORDER BY internal_id"),
            {"target_label": target_label},
        ).fetchall()

        print(f"Papers to process: {len(papers)}")

        # Step 2: Rebuild APA for each paper
        regenerated = 0
        for p in papers:
            pid, iid, title, year, jname, vol, pages, doi, fa, ca, doi_meta = p
            try:
                xref = json.loads(doi_meta or "{}")
            except Exception:
                xref = {}

            pd = {
                "first_authors": json.loads(fa) if isinstance(fa, str) else (fa or []),
                "corresponding_authors": json.loads(ca) if isinstance(ca, str) else (ca or []),
                "title": title,
                "year": year,
                "journal_name": jname,
                "volume": vol,
                "pages": pages,
                "doi": doi,
            }
            _build_apa_for_paper_data(pd, xref)
            apa = pd.get("apa_citation", "")
            if apa:
                session.execute(
                    text("UPDATE papers SET apa_citation = :apa WHERE id = :pid"),
                    {"apa": apa, "pid": pid},
                )
                regenerated += 1

            if regenerated % 100 == 0:
                print(f"  APA: {regenerated}/{len(papers)}", flush=True)

        session.commit()
        print(f"APA regenerated: {regenerated}")

        # Step 3: Export merged knowledge base
        print("\nExporting knowledge base...")
        papers = load_papers_for_export(session, internal_id_range=(1, 725))
        output_file = get_output_dir(ROOT) / "paper_library.md"

        stats = export_papers_merged(output_file, papers, include_full_text=False)
        print(f"Exported: {stats.exported} papers -> {output_file}")
        print(f"Skipped: {stats.skipped}, Errors: {len(stats.errors)}")
        for e in stats.errors[:5]:
            print(f"  {e}")


if __name__ == "__main__":
    main()
