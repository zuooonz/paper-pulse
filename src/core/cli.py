"""Shared helpers for command-line entry points."""

from __future__ import annotations

import sys
import os
from argparse import ArgumentParser, Namespace


def setup_console() -> None:
    """Use UTF-8 output only when the current console encoding needs it."""
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if not encoding or encoding.lower().replace("_", "-") in {"utf-8", "utf8"}:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Some wrapped streams do not support reconfiguration.
            continue


def add_db_arguments(parser: ArgumentParser) -> None:
    """Add shared database startup options to a command-line parser."""
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="跳过启动时的数据库迁移",
    )


def prepare_database(
    args: Namespace | None = None,
    *,
    workspace: str | None = None,
    config_dir: str | None = None,
) -> bool:
    """Bring the configured application database to the latest schema."""
    if os.environ.get("PAPER_PULSE_NO_MIGRATE") == "1" or getattr(
        args, "no_migrate", False
    ):
        return False

    from paper_library.db import ensure_database_current

    workspace = getattr(args, "workspace", None) or workspace or "."
    config_dir = getattr(args, "config_dir", None) or config_dir or "config"
    migrated = ensure_database_current(workspace, config_dir)
    if migrated:
        print("数据库已迁移到最新版本。")
    return migrated
