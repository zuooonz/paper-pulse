"""Word document parsing and manual upload draft lifecycle."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from paper_library.db import get_session
from paper_library.models import ArticleDraft
from tools import storage
from tools.word_parser import parse_docx
from tools.upload_limits import (
    UploadTooLarge,
    WORD_MAX_UPLOAD_BYTES,
    copy_upload,
    format_upload_limit,
    validate_upload_size,
)

logger = logging.getLogger(__name__)


class DraftUploadError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def upload_word(file, *, workspace: str, config_dir: str) -> dict:
    try:
        validate_upload_size(file, WORD_MAX_UPLOAD_BYTES, label="Word 文件")
    except UploadTooLarge as exc:
        file.file.close()
        raise DraftUploadError(
            f"Word 文件超过 {format_upload_limit(WORD_MAX_UPLOAD_BYTES)} 大小限制",
            status_code=413,
        ) from exc
    temp_dir = storage.get_temp_dir(workspace) / "word_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        try:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False, dir=temp_dir) as output:
                temp_path = Path(output.name)
                copy_upload(file, output, WORD_MAX_UPLOAD_BYTES)
        except UploadTooLarge as exc:
            raise DraftUploadError(
                f"Word 文件超过 {format_upload_limit(WORD_MAX_UPLOAD_BYTES)} 大小限制",
                status_code=413,
            ) from exc
        except Exception as exc:
            logger.exception("Failed to save uploaded Word file")
            raise DraftUploadError("Word 文件保存失败") from exc
        finally:
            file.file.close()

        with get_session(workspace=workspace, config_dir=config_dir) as session:
            draft = ArticleDraft(
                draft_type="manual_upload",
                title="",
                content="",
                status="draft",
                meta={"source": "word_upload", "original_filename": file.filename, "images": []},
            )
            session.add(draft)
            session.flush()
            draft_id = draft.id

        image_dir = None
        try:
            image_dir = storage.get_draft_image_dir(draft_id, workspace)
            image_dir.mkdir(parents=True, exist_ok=True)
            parsed = parse_docx(temp_path, image_dir, workspace=workspace)
            with get_session(workspace=workspace, config_dir=config_dir) as session:
                draft = session.get(ArticleDraft, draft_id)
                if draft is None:
                    raise RuntimeError("Draft disappeared during Word upload")
                draft.title = parsed["title"][:500]
                draft.content = parsed["content"]
                draft.meta = {
                    "source": "word_upload",
                    "original_filename": file.filename,
                    "images": parsed.get("image_rel_paths", []),
                }
                session.add(draft)
        except Exception as exc:
            logger.exception("Failed to parse or save uploaded Word file")
            try:
                with get_session(workspace=workspace, config_dir=config_dir) as session:
                    draft = session.get(ArticleDraft, draft_id)
                    if draft is not None:
                        session.delete(draft)
            except Exception:
                logger.exception("Failed to roll back the incomplete Word upload draft")
            if image_dir is not None:
                storage.safe_delete_dir(image_dir)
            raise DraftUploadError("Word 文件解析失败") from exc
        return {
            "ok": True,
            "draft_id": draft_id,
            "title": parsed["title"],
            "image_count": len(parsed.get("images", [])),
        }
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                logger.warning("清理 Word 上传临时文件失败", exc_info=True)
