"""Image normalization, publisher metadata, and thumbnail helpers for drafts."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path

from PIL import Image, ImageOps

from paper_library.cross_db import get_papers_for_draft
from paper_library.models import ArticleDraft
from tools import storage

logger = logging.getLogger(__name__)
IMAGE_THUMBNAIL_SIZE = 512
IMAGE_CACHE_MAX_AGE = 24 * 60 * 60


def normalize_image_path(value: str) -> str:
    return value.replace("\\", "/")


def _selected_images(paper) -> list[str]:
    selected = paper.selected_images or []
    if isinstance(selected, str):
        try:
            selected = json.loads(selected)
        except (TypeError, ValueError):
            selected = []
    if not isinstance(selected, list):
        return []
    return [normalize_image_path(path) for path in selected if isinstance(path, str) and path]


def _paper_image_data(paper, workspace: str | Path) -> tuple[list[tuple[dict, str]], str | None]:
    """Return extracted image entries and the best cover using one image pass."""
    extracted = paper.extracted_images or []
    if not isinstance(extracted, list):
        extracted = []
    entries: list[tuple[dict, str]] = []
    cover: str | None = None
    found_qualified_cover = False
    min_width, min_height = 372, 248
    for item in extracted:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path") or item.get("file_path", "")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = normalize_image_path(raw_path)
        entries.append((item, path))
        if cover is None:
            cover = path
        if not found_qualified_cover:
            resolved = storage.resolve_storage_path(raw_path, str(workspace))
            if resolved and resolved.exists():
                try:
                    with Image.open(resolved) as image:
                        if image.width >= min_width and image.height >= min_height:
                            cover = path
                            found_qualified_cover = True
                except Exception:
                    logger.warning("图片封面尺寸读取失败，已跳过", exc_info=True)
    return entries, cover


def _paper_images(paper, workspace: str | Path) -> tuple[list[str], str | None]:
    entries, cover = _paper_image_data(paper, workspace)
    return [path for _item, path in entries], cover


def pick_cover_image(paper, workspace: str | Path = ".") -> str | None:
    selected = _selected_images(paper)
    if selected:
        return selected[0]
    return _paper_images(paper, workspace)[1]


def collect_cover_images(paper, workspace: str | Path = ".") -> list[str]:
    selected = _selected_images(paper)
    if selected:
        return selected
    paths, cover = _paper_images(paper, workspace)
    if cover and paths and cover != paths[0]:
        paths.remove(cover)
        paths.insert(0, cover)
    return paths


def build_image_doi_map(draft: ArticleDraft, session, workspace: str | Path) -> dict[str, str]:
    """Build the DOI map through the shared draft image context."""
    return draft_image_context(draft, session, workspace)["image_doi_map"]


def draft_image_context(draft: ArticleDraft, session, workspace: str | Path, *, papers=None) -> dict:
    """Load linked papers and derive publisher and response image data together."""
    papers = papers if papers is not None else get_papers_for_draft(draft.id, session)
    cover_images: list[str] = []
    image_doi_map: dict[str, str] = {}
    paper_images: list[dict] = []
    paper_title = paper_abstract = paper_conclusions_zh = None
    paper_selected_images: list[str] = []
    multi_paper = len(papers) > 1
    cover_source = papers[0] if papers else None
    ws = str(Path(workspace).resolve())
    for paper in papers:
        selected = _selected_images(paper)
        extracted_entries, extracted_cover = _paper_image_data(paper, workspace)
        extracted_paths = [path for _item, path in extracted_entries]
        if paper is cover_source:
            cover_images = selected or list(extracted_paths)
            if not selected and extracted_cover and cover_images and extracted_cover != cover_images[0]:
                cover_images.remove(extracted_cover)
                cover_images.insert(0, extracted_cover)
        doi = (paper.doi or "").strip()
        if doi:
            for path in selected + extracted_paths:
                image_doi_map[path] = doi
        if paper_title is None:
            paper_title = paper.title
            paper_abstract = paper.abstract_zh or paper.abstract
            paper_conclusions_zh = paper.conclusions_zh
            paper_selected_images = selected
        for item, path in extracted_entries:
            raw_path = item.get("path") or item.get("file_path", "")
            resolved, normalized = storage.resolve_image_path_for_response(raw_path, ws)
            size_kb = round(resolved.stat().st_size / 1024, 1) if resolved else 0
            caption = item.get("caption") or item.get("description", "")
            if multi_paper:
                label = f"[Cite {paper.internal_id or paper.id}] "
                caption = label + caption if caption else label
            paper_images.append({"path": normalized or path, "caption": caption, "size_kb": size_kb})

    if not papers and isinstance(draft.meta, dict):
        for raw_path in draft.meta.get("images", []):
            if not isinstance(raw_path, str) or not raw_path:
                continue
            resolved, normalized = storage.resolve_image_path_for_response(raw_path, ws)
            size_kb = round(resolved.stat().st_size / 1024, 1) if resolved else 0
            paper_images.append({"path": normalized or normalize_image_path(raw_path), "caption": "", "size_kb": size_kb})
    return {
        "papers": papers,
        "cover_images": cover_images,
        "image_doi_map": image_doi_map,
        "paper_title": paper_title,
        "paper_abstract": paper_abstract,
        "paper_conclusions_zh": paper_conclusions_zh,
        "paper_selected_images": paper_selected_images,
        "paper_images": paper_images,
    }


def get_image_thumbnail(source: Path, workspace: Path, size: int) -> Path:
    """Create a cached JPEG thumbnail without modifying the publisher source."""
    stat = source.stat()
    cache_key = hashlib.sha256(
        f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{size}".encode("utf-8")
    ).hexdigest()
    cache_dir = workspace / "data" / "cache" / "image_thumbnails"
    cache_dir.mkdir(parents=True, exist_ok=True)
    thumbnail = cache_dir / f"{cache_key}.jpg"
    if thumbnail.exists():
        return thumbnail
    temporary = cache_dir / f"{cache_key}.{threading.get_ident()}.tmp"
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(temporary, format="JPEG", quality=82, optimize=True)
        try:
            temporary.replace(thumbnail)
        except FileExistsError:
            temporary.unlink(missing_ok=True)
        return thumbnail
    finally:
        temporary.unlink(missing_ok=True)


def resolve_image_request(image_path: str, workspace: Path) -> tuple[Path | None, str | None]:
    """Resolve and confine an image path; return the failure category if absent."""
    safe_path = normalize_image_path(image_path)
    if ".." in safe_path:
        return None, "invalid"
    img_path = storage.resolve_storage_path(safe_path, workspace)
    if img_path is None:
        return None, "missing"
    image_root = storage.get_image_root(workspace)
    resolved = img_path.resolve()
    roots = (workspace.resolve(), image_root.resolve())
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None, "outside"
    if not img_path.exists():
        img_path = storage.find_image_with_fallback(img_path, workspace)
    if not img_path or not img_path.exists():
        return None, "missing"
    return img_path, None
