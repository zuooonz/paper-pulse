from __future__ import annotations
import logging

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict
from typing import List, Tuple

from core.service_config import configured_value, warn_missing

logger = logging.getLogger(__name__)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop an isolated extractor process group and wait for it to exit."""
    if process.poll() is not None:
        process.wait(timeout=1)
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Could not stop PDF extractor process tree", exc_info=True)
        if process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        process.wait(timeout=5)

def _filter_small_image_refs(markdown_text: str, md_path: Path, min_image_bytes: int) -> str:
    md_img_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    out_lines: List[str] = []
    for raw in markdown_text.splitlines():
        line = raw.rstrip("\n")
        m = md_img_pattern.search(line)
        if not m:
            out_lines.append(line)
            continue
        ref = m.group(1).strip().strip('"').strip("'")
        if not ref:
            continue
        p = (md_path.parent / ref).resolve() if not Path(ref).is_absolute() else Path(ref)
        try:
            if p.exists() and p.stat().st_size >= min_image_bytes:
                out_lines.append(line)
        except Exception:
            # If size cannot be determined, drop it to avoid fragmented/noisy images.
            logger.warning('处理失败，已跳过该项（_filter_small_image_refs）', exc_info=True)
            continue
    return "\n".join(out_lines)


def extract_text_and_images(pdf_path: str | None, image_output_dir: Path, max_images: int = 12) -> Tuple[str, List[Dict[str, Any]]]:
    if not pdf_path:
        return "", []

    p = Path(pdf_path)
    if not p.exists():
        return "", []

    text_chunks: List[str] = []
    images: List[Dict[str, Any]] = []

    try:
        import pymupdf  # PyMuPDF
    except Exception:
        # If PyMuPDF is unavailable, return empty extraction gracefully.
        logger.debug("PyMuPDF is unavailable; skipping local PDF extraction", exc_info=True)
        return "", []

    image_output_dir.mkdir(parents=True, exist_ok=True)
    cwd = Path.cwd()

    with pymupdf.open(p) as doc:
        for page_index, page in enumerate(doc):
            page_text = page.get_text("text")
            text_chunks.append(page_text)
            if len(images) >= max_images:
                continue

            # Find captions on this page
            captions = re.findall(r'(?:Figure|Fig\.?|图)\s*\d+[.:：]?\s*.{0,120}', page_text, re.IGNORECASE)
            caption = captions[0].strip() if captions else ""

            for image_index, img in enumerate(page.get_images(full=True)):
                if len(images) >= max_images:
                    break
                xref = img[0]
                base = doc.extract_image(xref)
                img_bytes = base.get("image")
                ext = base.get("ext", "png")
                out_path = image_output_dir / f"p{page_index+1}_img{image_index+1}.{ext}"
                out_path.write_bytes(img_bytes)

                # Calculate relative path from workspace root (cwd), always use forward slashes
                rel_path = str(out_path.relative_to(cwd)).replace("\\", "/") if out_path.is_relative_to(cwd) else str(out_path).replace("\\", "/")

                images.append({
                    "path": rel_path,
                    "page": page_index + 1,
                    "index": image_index + 1,
                    "size_kb": round(len(img_bytes) / 1024, 1),
                    "caption": caption
                })

    text = "\n".join(text_chunks).strip()
    return text, images


def _extract_with_mineru(
    pdf_path: Path,
    image_output_dir: Path,
    max_images: int,
    mineru_cfg: Dict[str, Any],
) -> Tuple[str, List[str], str]:
    mode = str(mineru_cfg.get("mode", "local")).strip().lower()
    backend = str(mineru_cfg.get("backend", "pipeline")).strip()
    method = str(mineru_cfg.get("method", "")).strip().lower()
    if method not in {"auto", "txt", "ocr"}:
        return "", [], f"mineru_invalid_method:{method or 'empty'}"
    lang = str(mineru_cfg.get("lang", "en")).strip()
    device_mode = str(mineru_cfg.get("device_mode", "cpu")).strip().lower()
    enable_formula_rec = bool(mineru_cfg.get("enable_formula_rec", True))
    is_scan = bool(mineru_cfg.get("is_scan", False))
    timeout_seconds = int(mineru_cfg.get("timeout_seconds", 900))
    stall_timeout_seconds = int(mineru_cfg.get("stall_timeout_seconds", timeout_seconds))
    api_url = str(mineru_cfg.get("base_url") or configured_value("MINERU_BASE_URL", "extractor", "mineru", "base_url")).strip()
    api_token = str(mineru_cfg.get("api_token") or configured_value("MINERU_API_TOKEN", "extractor", "mineru", "api_token")).strip()
    if mode == "remote" and (not api_url or not api_token):
        warn_missing("MinerU remote extraction", "MINERU_BASE_URL and MINERU_API_TOKEN")
        return "", [], "mineru_remote_not_configured"

    image_output_dir.mkdir(parents=True, exist_ok=True)
    out_dir = image_output_dir.parent / "mineru"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check if we can reuse existing mineru output from a previous run or reuse-dir.
    existing_mds = sorted(out_dir.rglob("*.md"))
    skip_command = False
    if existing_mds:
        # If there's at least one .md file, consider it as a reuse candidate.
        # This prevents redundant re-runs when cache_path is missing but mineru dir is present.
        skip_command = True

    timeout_reason = ""
    if not skip_command:
        cmd = [
            "mineru",
            "-p",
            str(pdf_path),
            "-o",
            str(out_dir),
            "-b",
            backend,
            "-m",
            method,
            "-l",
            lang,
            "--enable_formula",
            "true" if enable_formula_rec else "false",
            "--is_scan",
            "true" if is_scan else "false",
        ]

        if mode == "remote" and api_url:
            cmd.extend(["-u", api_url])

        env = os.environ.copy()
        if device_mode:
            env["MINERU_DEVICE_MODE"] = device_mode
        if api_token:
            env["MINERU_API_TOKEN"] = api_token

        stdout_log = image_output_dir.parent / "mineru_stdout.log"
        stderr_log = image_output_dir.parent / "mineru_stderr.log"

        def _latest_output_mtime(root: Path) -> float:
            latest = 0.0
            try:
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        mt = p.stat().st_mtime
                    except Exception:
                        logger.warning('处理失败，已跳过该项（_latest_output_mtime）', exc_info=True)
                        continue
                    if mt > latest:
                        latest = mt
            except Exception:
                logger.debug("Output timestamp lookup failed", exc_info=True)
                return 0.0
            return latest

        proc = None
        try:
            with stdout_log.open("a", encoding="utf-8", errors="ignore") as out_f, stderr_log.open(
                "a", encoding="utf-8", errors="ignore"
            ) as err_f:
                out_f.write(f"\n--- NEW EXTRACTION START AT {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                creationflags = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    if os.name == "nt"
                    else 0
                )
                proc = subprocess.Popen(
                    cmd,
                    stdout=out_f,
                    stderr=err_f,
                    text=True,
                    env=env,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )

                started_at = time.monotonic()
                last_progress_at = started_at
                last_mtime = _latest_output_mtime(out_dir)

                while True:
                    rc = proc.poll()
                    if rc is not None:
                        break

                    now = time.monotonic()
                    if timeout_seconds > 0 and (now - started_at) > timeout_seconds:
                        timeout_reason = f"mineru_timeout_{timeout_seconds}s"
                        _terminate_process_tree(proc)
                        break

                    new_mtime = _latest_output_mtime(out_dir)
                    if new_mtime > last_mtime:
                        last_mtime = new_mtime
                        last_progress_at = now

                    if stall_timeout_seconds > 0 and (now - last_progress_at) > stall_timeout_seconds:
                        timeout_reason = f"mineru_stall_timeout_{stall_timeout_seconds}s"
                        _terminate_process_tree(proc)
                        break

                    time.sleep(1.0)
            
            if proc is not None:
                proc.wait()
            else:
                return "", [], "mineru_spawn_failed"

        except Exception as e:
            # If mineru command is missing, we fail gracefully to fallback (unless we skipped)
            return "", [], f"mineru_exec_error:{str(e)}"

    if not timeout_reason and skip_command:
        md_files = sorted(out_dir.rglob("*.md"))
        if not md_files:
            # We thought we could skip, but no .md found. This is an invalid skip state.
            return "", [], "mineru_no_md_found"
    elif not timeout_reason and proc and proc.returncode == 0:
        md_files = sorted(out_dir.rglob("*.md"))
    elif timeout_reason:
        return "", [], timeout_reason
    else:
        return "", [], f"mineru_rc_{proc.returncode if proc else 'None'}"

    if not md_files:
        return "", [], "mineru_empty_dir"

    text_chunks: List[str] = []
    md_refs: List[Path] = []
    md_img_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    min_image_bytes = int(mineru_cfg.get("min_image_bytes", 8 * 1024))
    for md in md_files:
        try:
            content = md.read_text(encoding="utf-8", errors="ignore")
            content = _filter_small_image_refs(content, md, min_image_bytes)
            text_chunks.append(content)
            for m in md_img_pattern.finditer(content):
                ref = m.group(1).strip().strip('"').strip("'")
                if not ref:
                    continue
                # Prefer images actually referenced by MinerU markdown.
                candidate = (md.parent / ref).resolve() if not Path(ref).is_absolute() else Path(ref)
                md_refs.append(candidate)
        except Exception:
            logger.warning('处理失败，已跳过该项（_extract_with_mineru）', exc_info=True)
            continue

    seen: set[Path] = set()
    chosen: List[Path] = []

    def _accept(p: Path) -> bool:
        if not p.exists() or not p.is_file():
            return False
        suffix = p.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            return False
        try:
            if p.stat().st_size < min_image_bytes:
                return False
        except Exception:
            logger.debug("Image size check failed", exc_info=True)
            return False
        return True

    for ref in md_refs:
        rp = ref.resolve()
        if rp in seen:
            continue
        if _accept(rp):
            chosen.append(rp)
            seen.add(rp)
        if len(chosen) >= max_images:
            break

    if len(chosen) < max_images:
        image_candidates = (
            sorted(out_dir.rglob("*.png"))
            + sorted(out_dir.rglob("*.jpg"))
            + sorted(out_dir.rglob("*.jpeg"))
            + sorted(out_dir.rglob("*.webp"))
        )
        # Fallback: larger images are more likely to be full figures than fragments/icons.
        image_candidates = sorted(image_candidates, key=lambda x: x.stat().st_size if x.exists() else 0, reverse=True)
        for p in image_candidates:
            rp = p.resolve()
            if rp in seen:
                continue
            if _accept(rp):
                chosen.append(rp)
                seen.add(rp)
            if len(chosen) >= max_images:
                break

    image_paths = [str(p) for p in chosen]
    text = "\n\n".join(text_chunks).strip()
    if not text and not image_paths:
        return "", [], "mineru_empty_output"
    return text, image_paths, "ok"


def extract_pdf_content(
    pdf_path: str | None,
    image_output_dir: Path,
    max_images: int = 12,
    extractor_cfg: Dict[str, Any] | None = None,
) -> Tuple[str, List[Any], str]:
    if not pdf_path:
        return "", [], "none"

    p = Path(pdf_path)
    if not p.exists():
        return "", [], "missing"

    cfg = extractor_cfg or {}
    selected = str(cfg.get("default", "pymupdf")).strip().lower()
    fallback_to_pymupdf = bool(cfg.get("fallback_to_pymupdf", True))
    mineru_cfg = cfg.get("mineru", {})

    if selected == "mineru" and bool(mineru_cfg.get("enabled", True)):
        text, images, reason = _extract_with_mineru(p, image_output_dir, max_images, mineru_cfg)
        if text or images:
            return text, images, "mineru"
        if not fallback_to_pymupdf:
            return "", [], f"mineru_failed:{reason}"

    text, images = extract_text_and_images(str(p), image_output_dir, max_images=max_images)
    if selected == "mineru":
        return text, images, "pymupdf_fallback"
    return text, images, "pymupdf"
