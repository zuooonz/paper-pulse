#!/usr/bin/env python3
"""清理 PDF 目录中没有论文记录对应的文件。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.cli import add_db_arguments, prepare_database
from paper_library.db import get_session_factory  # noqa: E402
from paper_library.models import Paper  # noqa: E402
from tools.storage import get_pdf_dir, resolve_storage_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_db_arguments(parser)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--apply", action="store_true", help="删除已确认的孤儿 PDF")
    args = parser.parse_args()
    prepare_database(args, workspace=str(ROOT))

    workspace = args.workspace.resolve()
    pdf_dir = get_pdf_dir(workspace)
    session_factory = get_session_factory(str(workspace), args.config_dir)
    with session_factory() as session:
        stored_paths = session.query(Paper.pdf_storage_path).filter(
            Paper.pdf_storage_path.isnot(None)
        ).all()
    db_paths = {
        resolved
        for (stored_path,) in stored_paths
        if (resolved := resolve_storage_path(stored_path, workspace)) is not None
    }
    orphans = sorted(path.resolve() for path in pdf_dir.glob("*.pdf") if path.resolve() not in db_paths)

    print(f"发现 {len(orphans)} 个孤儿 PDF 文件")
    for path in orphans:
        action = "删除" if args.apply else "待删除"
        print(f"  {action}: {path.name}")
        if args.apply:
            path.unlink()
    if not args.apply:
        print("预览模式；确认后添加 --apply 删除文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
