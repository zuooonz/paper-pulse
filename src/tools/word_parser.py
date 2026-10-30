"""Parse .docx files into Markdown + extracted images.

Supports:
- Headings (Heading 1/2/3 → # / ## / ###)
- Bold / italic / underline → ** / * / <u>
- Bullet / numbered lists
- Image extraction (saved to disk)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn


def _parse_runs(paragraph) -> str:
    """Convert a paragraph's runs to Markdown-ish text with inline formatting."""
    parts: List[str] = []
    for run in paragraph.runs:
        text = run.text
        if not text:
            continue
        # Escape markdown special chars
        text = text.replace("*", "\\*").replace("_", "\\_")
        if run.bold:
            text = f"**{text}**"
        if run.italic:
            text = f"*{text}*"
        if run.underline:
            text = f"<u>{text}</u>"
        parts.append(text)
    return "".join(parts)


def _extract_images(doc: Document, output_dir: Path) -> List[str]:
    """Extract all images from a docx and save them to output_dir.

    Returns list of saved file paths (relative to workspace).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: List[str] = []

    # docx images are stored as related image parts
    image_parts = []
    seen = set()
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            image_parts.append(rel.target_part)

    for idx, image_part in enumerate(image_parts, start=1):
        # Determine extension from content type
        ext = _image_ext_from_ct(image_part.content_type)
        filename = f"image_{idx:03d}{ext}"
        dest = output_dir / filename

        # Avoid overwriting if same blob already exists
        blob = image_part.blob
        if blob in seen:
            continue
        seen.add(blob)

        with open(dest, "wb") as f:
            f.write(blob)
        saved.append(str(dest))

    return saved


def _image_ext_from_ct(content_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    return mapping.get(content_type, ".png")


def _is_bullet(paragraph) -> bool:
    """Check if paragraph is a bullet list item."""
    pPr = paragraph._p.get_or_add_pPr()
    return pPr.find(qn("w:numPr")) is not None


def _list_level(paragraph) -> int:
    """Return list indentation level (0-based)."""
    pPr = paragraph._p.get_or_add_pPr()
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return 0
    ilvl = numPr.find(qn("w:ilvl"))
    if ilvl is not None:
        val = ilvl.get(qn("w:val"))
        if val is not None:
            return int(val)
    return 0


def _is_numbered(paragraph) -> bool:
    """Check if paragraph is a numbered list item (not bullet)."""
    pPr = paragraph._p.get_or_add_pPr()
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return False
    numId = numPr.find(qn("w:numId"))
    if numId is None:
        return False
    # Bullets typically use numId >= 1, but we can't easily distinguish
    # without looking at the numbering definition.  Heuristic: if the
    # paragraph text starts with a digit + dot, treat as numbered.
    text = paragraph.text.strip()
    return bool(re.match(r"^\d+[.\)]\s", text))


def parse_docx(
    file_path: str | Path,
    image_output_dir: str | Path,
    workspace: str | Path = ".",
) -> dict:
    """Parse a .docx file into structured data.

    Args:
        file_path: Path to the .docx file.
        image_output_dir: Directory to save extracted images.
        workspace: Project root for computing relative paths.

    Returns:
        {
            "title": str,           # First Heading 1 or first line
            "content": str,         # Full Markdown content
            "images": [str],        # Absolute paths of extracted images
            "image_rel_paths": [str],  # Paths relative to workspace
        }
    """
    file_path = Path(file_path)
    image_output_dir = Path(image_output_dir)
    workspace = Path(workspace).resolve()

    doc = Document(str(file_path))

    lines: List[str] = []
    title = ""
    for para in doc.paragraphs:
        style_name = (para.style.name or "").strip()
        text = _parse_runs(para)
        raw_text = para.text.strip()

        if not raw_text and not para.runs:
            lines.append("")
            continue

        # Title extraction
        if (style_name.startswith("Heading 1") or style_name == "Title") and not title:
            title = raw_text

        # Heading mapping
        if style_name == "Title" or style_name.startswith("Heading 1"):
            lines.append(f"# {text}")
        elif style_name.startswith("Heading 2"):
            lines.append(f"## {text}")
        elif style_name.startswith("Heading 3"):
            lines.append(f"### {text}")
        elif style_name.startswith("Heading 4"):
            lines.append(f"#### {text}")
        elif _is_bullet(para):
            level = _list_level(para)
            indent = "  " * level
            lines.append(f"{indent}- {text}")
        elif _is_numbered(para):
            level = _list_level(para)
            indent = "  " * level
            # Strip leading number if present
            clean = re.sub(r"^\d+[.\)]\s*", "", text)
            lines.append(f"{indent}1. {clean}")
        else:
            # Alignment hints
            align = para.alignment
            if align == WD_PARAGRAPH_ALIGNMENT.CENTER:
                lines.append(f'<p align="center">{text}</p>')
            elif align == WD_PARAGRAPH_ALIGNMENT.RIGHT:
                lines.append(f'<p align="right">{text}</p>')
            else:
                lines.append(text)

    # Extract images
    image_paths = _extract_images(doc, image_output_dir)
    image_rel_paths = []
    for p in image_paths:
        abs_p = Path(p).resolve()
        try:
            rel = abs_p.relative_to(workspace)
            image_rel_paths.append(str(rel).replace("\\", "/"))
        except ValueError:
            image_rel_paths.append(str(abs_p).replace("\\", "/"))

    content = "\n\n".join(lines)

    # Fallback title: first non-empty line if no Heading 1
    if not title:
        for line in lines:
            stripped = line.strip()
            if stripped:
                title = stripped.lstrip("# ").strip()
                break

    return {
        "title": title or "未命名草稿",
        "content": content,
        "images": image_paths,
        "image_rel_paths": image_rel_paths,
    }
