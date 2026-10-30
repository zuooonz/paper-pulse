"""统一文件存储路径解析与生命周期管理工具

提供统一文件/图片路径解析，目录可在 config/runtime.yaml 的 storage 段配置。

规范：
- 数据库优先存储相对于 workspace 的 Unix 风格路径（正斜杠）
- 默认目录为 data/pdf、data/images、data/tmp 和 output

"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from core.service_config import resolve_storage_path as _resolve_configured_root

logger = logging.getLogger(__name__)

# ── 目录常量 ───────────────────────────────────────────────────
PDF_DIR = "data/pdf"
CACHE_DIR = "data/cache"
PAPER_IMAGE_DIR_PATTERN = "data/images/{paper_id}"
DRAFT_IMAGE_DIR_PATTERN = "data/images/draft_{draft_id}"


# ── 路径解析 ───────────────────────────────────────────────────

def resolve_storage_path(storage_path: str | None, workspace: str | Path = ".") -> Path | None:
    """将数据库中存储的路径字符串解析为绝对 Path。

    兼容：
    - None / 空字符串 → None
    - 相对路径（如 data/pdf/100_xxx.pdf）→ 相对于 workspace 解析
    - Windows 绝对路径（如 D:/Docs/.../data/pdf/xxx.pdf）→
      在 Windows 上直接解析；在 Linux 上降级为相对于 workspace 的 data/pdf/ 查找
    - Unix 绝对路径 → 直接解析
    """
    if not storage_path:
        return None

    workspace = Path(workspace).resolve()
    safe_path = storage_path.replace("\\", "/").strip()

    # Windows 绝对路径检测 (D:/...)
    if len(safe_path) >= 2 and safe_path[1] == ":":
        p = Path(safe_path)
        if p.exists():
            return p.resolve()
        # 降级：在非 Windows 系统上，尝试从路径中提取相对部分
        # 例如 D:/Docs/Project/data/pdf/100.pdf → data/pdf/100.pdf
        if os.name != "nt":
            for anchor in ("/data/", "/output/", "/config/"):
                idx = safe_path.lower().find(anchor)
                if idx != -1:
                    rel = safe_path[idx + 1 :]  # 去掉前导 /
                    candidate = workspace / rel
                    if candidate.exists():
                        return candidate.resolve()
        return None

    # Unix 绝对路径
    if safe_path.startswith("/"):
        p = Path(safe_path)
        if p.exists():
            return p.resolve()
        # 如果绝对路径不存在，尝试相对于 workspace（常见于容器/服务环境 cwd 不同）
        candidate = workspace / safe_path.lstrip("/")
        if candidate.exists():
            return candidate.resolve()
        return None

    # 相对路径
    return (workspace / safe_path).resolve()


def ensure_relative_path(abs_path: Path | str, workspace: str | Path = ".") -> str:
    """将绝对路径转为相对于 workspace 的 Unix 风格路径，用于存入数据库。

    如果路径已经在 workspace 内，返回相对路径；否则返回原路径的正斜杠版本。
    """
    workspace = Path(workspace).resolve()
    p = Path(abs_path).resolve()
    try:
        rel = p.relative_to(workspace)
    except ValueError:
        rel = p
    return str(rel).replace("\\", "/")


def normalize_path_for_db(path_str: str | None, workspace: str | Path = ".") -> str | None:
    """清理可能混入的绝对路径，确保存入 DB 的是规范相对路径。"""
    if not path_str:
        return None
    resolved = resolve_storage_path(path_str, workspace)
    if resolved is None:
        return path_str.replace("\\", "/")  # 至少做正斜杠标准化
    return ensure_relative_path(resolved, workspace)


# ── 文件生命周期 ───────────────────────────────────────────────

def safe_delete_file(path: Path | str) -> bool:
    """安全删除单个文件，返回是否成功。"""
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
            logger.debug("Deleted file: %s", p)
            return True
    except Exception as e:
        logger.warning("Failed to delete file %s: %s", path, e)
    return False


def safe_delete_dir(path: Path | str, only_if_empty: bool = False) -> bool:
    """安全删除目录。only_if_empty=True 时仅删除空目录。"""
    try:
        p = Path(path)
        if not p.exists() or not p.is_dir():
            return False
        if only_if_empty:
            if not any(p.iterdir()):
                p.rmdir()
                logger.debug("Deleted empty dir: %s", p)
                return True
            return False
        shutil.rmtree(p)
        logger.debug("Deleted dir tree: %s", p)
        return True
    except Exception as e:
        logger.warning("Failed to delete dir %s: %s", path, e)
    return False


# ── 目录便捷函数 ───────────────────────────────────────────────

def get_storage_root(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("root", workspace)


def get_pdf_dir(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("pdf_root", workspace)


def get_image_root(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("images_root", workspace)


def get_paper_image_dir(paper_id: int, workspace: str | Path = ".") -> Path:
    return get_image_root(workspace) / str(paper_id)


def get_draft_image_dir(draft_id: int, workspace: str | Path = ".") -> Path:
    return get_image_root(workspace) / f"draft_{draft_id}"


def get_cache_dir(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("cache_root", workspace)


def get_temp_dir(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("temp_root", workspace)


def get_output_dir(workspace: str | Path = ".") -> Path:
    return _resolve_configured_root("output_root", workspace)


# ── 图片 fallback 搜索 ─────────────────────────────────────────

def find_image_with_fallback(image_path: Path, workspace: str | Path = ".") -> Path | None:
    """查找图片文件；如果直接路径不存在且在 data/images/ 下，搜索子目录。"""
    if image_path.exists():
        return image_path.resolve()

    safe_str = str(image_path).replace("\\", "/")
    image_root = get_image_root(workspace)
    image_root_names = {str(image_root).replace("\\", "/").lower(), "data/images/"}
    try:
        image_root_names.add(str(image_root.relative_to(Path(workspace).resolve())).replace("\\", "/").lower())
    except ValueError:
        pass
    if not any(root_name in safe_str.lower() for root_name in image_root_names):
        return None

    workspace = Path(workspace).resolve()
    cache_dir = image_root
    if not cache_dir.exists():
        return None

    fname = image_path.name
    for subdir in cache_dir.iterdir():
        if subdir.is_dir():
            candidate = subdir / fname
            if candidate.exists():
                return candidate.resolve()
    return None


def resolve_image_path_for_response(
    img_path_str: str,
    workspace: str | Path = ".",
    fallback_subdir_search: bool = True,
) -> tuple[Path | None, str]:
    """解析图片路径用于 API 响应。

    返回 (resolved_path, normalized_path_str)。
    normalized_path_str 是相对于 workspace 的 Unix 风格路径，用于返回给前端。
    """
    if not img_path_str:
        return None, ""

    workspace = Path(workspace).resolve()
    safe = img_path_str.replace("\\", "/")
    p = resolve_storage_path(safe, workspace)

    if p is None:
        return None, safe

    if fallback_subdir_search and not p.exists():
        p = find_image_with_fallback(p, workspace)

    if p and p.exists():
        norm = ensure_relative_path(p, workspace)
        return p, norm

    return None, safe
