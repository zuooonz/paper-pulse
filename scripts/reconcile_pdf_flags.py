#!/usr/bin/env python3
"""分批核对 PDF 文件并修正论文的 has_pdf 标志。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database  # noqa: E402
from paper_library.db import get_session  # noqa: E402
from paper_library.paper_lifecycle import reconcile_pdf_flags  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    workspace = args.workspace.resolve()
    with get_session(str(workspace), args.config_dir) as session:
        corrected = reconcile_pdf_flags(session, workspace)
    print(f"已修正 {corrected} 条 PDF 可用标志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
