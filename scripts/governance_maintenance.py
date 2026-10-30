"""Back up app.db and archive old audit events.

Examples:
    python scripts/governance_maintenance.py --backup
    python scripts/governance_maintenance.py --archive-before 2026-01-01
    python scripts/governance_maintenance.py --archive-before 2026-01-01 --delete-archived-views
"""

from __future__ import annotations

import argparse
import datetime as dt
import json

from core.cli import add_db_arguments, prepare_database
from paper_library.governance_maintenance import archive_audit_events, backup_governance_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Application DB backup and audit archive")
    parser.add_argument("--backup", action="store_true", help="create a consistent SQLite app.db backup")
    parser.add_argument("--archive-before", type=dt.date.fromisoformat, help="archive events before YYYY-MM-DD")
    parser.add_argument(
        "--delete-archived-views",
        action="store_true",
        help="after archive, delete only old paper.view and artifact-download events",
    )
    add_db_arguments(parser)
    args = parser.parse_args()
    prepare_database(args, workspace=".")
    if not args.backup and not args.archive_before:
        parser.error("choose --backup and/or --archive-before")
    result: dict[str, object] = {}
    if args.backup:
        backup_path = backup_governance_database()
        result["backup"] = str(backup_path.resolve()) if backup_path else None
    if args.archive_before:
        result["archive"] = archive_audit_events(
            args.archive_before,
            delete_archived_views=args.delete_archived_views,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
