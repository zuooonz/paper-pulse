# -*- coding: utf-8 -*-
"""显式下载并安装 ShowJCR release 中的 jcr.db。"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import requests

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from paper_library.journal_enrichment import JcrMatcher
from core.net_safety import safe_get

GITHUB_API_URL = "https://api.github.com/repos/hitfyd/ShowJCR/releases/latest"


def get_reference_db_path(config_path: str | Path | None = None) -> Path:
    """Resolve the target using REFERENCE_CONFIG and JOURNAL_METRICS_DB."""
    path = JcrMatcher(config_path=config_path).jcr_db_path
    if path is None:
        raise RuntimeError("参考配置未设置 journal_metrics.path")
    return path


def _latest_release_asset() -> str:
    response = requests.get(GITHUB_API_URL, timeout=30)
    response.raise_for_status()
    release = response.json()
    for asset in release.get("assets", []):
        if asset.get("name") == "ShowJCR.7z":
            return str(asset.get("browser_download_url") or "")
    raise RuntimeError("ShowJCR 最新 release 中未找到 ShowJCR.7z")


def download_and_extract_jcr_db(
    download_url: str,
    destination: Path,
) -> None:
    """Download an archive to a temporary directory and install its jcr.db."""
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError(
            "缺少 py7zr，请安装项目的可选工具依赖 paper-pulse[tools]"
        ) from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="showjcr-", dir=destination.parent) as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / "ShowJCR.7z"
        response = safe_get(requests, download_url, stream=True, timeout=180)
        response.raise_for_status()
        with archive_path.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    stream.write(chunk)

        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            db_files = [name for name in archive.getnames() if Path(name).name.casefold() == "jcr.db"]
            if not db_files:
                raise RuntimeError("ShowJCR.7z 中未找到 jcr.db")
            extract_dir = temp_dir / "extracted"
            extract_dir.mkdir()
            archive.extract(path=extract_dir, targets=[db_files[0]])

        extracted = extract_dir / db_files[0]
        if not extracted.is_file():
            candidates = list(extract_dir.rglob("jcr.db"))
            if not candidates:
                raise RuntimeError("解压后未找到 jcr.db")
            extracted = candidates[0]
        staged = temp_dir / "jcr.db"
        shutil.copy2(extracted, staged)
        staged.replace(destination)


def update_database(config_path: str | Path | None = None) -> Path:
    try:
        import py7zr  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "缺少 py7zr，请安装项目的可选工具依赖 paper-pulse[tools]"
        ) from exc
    destination = get_reference_db_path(config_path)
    print(f"目标路径：{destination}")
    print("正在查询 ShowJCR 最新 release…")
    download_url = _latest_release_asset()
    if not download_url:
        raise RuntimeError("ShowJCR release 下载地址为空")
    print("正在下载并解压 jcr.db…")
    download_and_extract_jcr_db(download_url, destination)
    print(f"jcr.db 已更新：{destination}")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手动更新 ShowJCR 参考数据库")
    parser.add_argument("--force", action="store_true", help="兼容显式强制更新命令；每次执行本工具都会更新")
    parser.add_argument("--config", default=None, help="参考数据 YAML 路径；默认使用 REFERENCE_CONFIG 或 config/reference.yaml")
    args = parser.parse_args(argv)
    try:
        update_database(args.config)
    except Exception as exc:
        print(f"更新失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
