"""批量为草稿打分，写入 meta['quality_score']"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import argparse
from sqlalchemy import select
from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session
from paper_library.models import ArticleDraft
from core.quality_scorer import score_draft

def main():
    parser = argparse.ArgumentParser()
    add_db_arguments(parser)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    with get_session(args.workspace, args.config_dir) as session:
        drafts = list(session.scalars(select(ArticleDraft)).all())
        updated = 0
        for d in drafts:
            if not d.content:
                continue
            total, dims = score_draft(d.content)
            meta = d.meta if isinstance(d.meta, dict) else {}
            meta["quality_score"] = {"total": total, **dims}
            d.meta = meta
            updated += 1
        if not args.dry_run:
            session.commit()
        print(f"Scored {updated} drafts (dry_run={args.dry_run})")

if __name__ == "__main__":
    main()
