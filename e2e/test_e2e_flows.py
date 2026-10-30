"""Live end-to-end checks; skipped unless both E2E URLs are configured."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import struct
import subprocess
import sys
import time
import uuid
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
E2E_ENABLED = bool(os.environ.get("E2E_BASE_URL") and os.environ.get("E2E_API_URL"))
pytestmark = pytest.mark.skipif(
    not E2E_ENABLED,
    reason="设置 E2E_BASE_URL 和 E2E_API_URL 后才运行实时端到端测试",
)

if E2E_ENABLED:
    import httpx
    from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
else:  # Keep the default skipped collection independent of optional browser packages.
    httpx = None  # type: ignore[assignment]
    Browser = BrowserContext = Page = Any  # type: ignore[misc,assignment]
    sync_playwright = None


PASSWORDS = {
    "admin": os.environ.get("E2E_PASSWORD_ADMIN", "E2E-test-admin-2026!"),
    "reviewer": os.environ.get("E2E_PASSWORD_REVIEWER", "E2E-test-reviewer-2026!"),
    "viewer": os.environ.get("E2E_PASSWORD_VIEWER", "E2E-test-viewer-2026!"),
    "limited": os.environ.get("E2E_PASSWORD_LIMITED", "E2E-test-limited-2026!"),
}
ROLE_NAMES = {"admin": "管理员", "reviewer": "审核员", "viewer": "查看者", "limited": "受限用户"}


def _png_sample() -> bytes:
    """Make a tiny RGB PNG without adding an image-library dependency."""
    width = height = 32
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            if (x // 8 + y // 8) % 2:
                row.extend((49, 111, 156))
            else:
                row.extend((218, 235, 244))
        rows.append(bytes(row))

    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )


def _make_sample_pdf() -> Path:
    pymupdf = pytest.importorskip("pymupdf", reason="生成 E2E PDF 需要 PyMuPDF")
    fixture = Path(__file__).resolve().parent / "fixtures" / "sample.pdf"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((64, 68), "Reproducible qMRI relaxometry with qMRLab software", fontsize=16)
    page.insert_text((64, 110), "Abstract", fontsize=12)
    page.insert_textbox(
        pymupdf.Rect(64, 125, 540, 220),
        "This fictional qMRI study evaluates repeatability of quantitative MRI parameter maps. "
        "The example contains no patient data and exists only for software testing.",
        fontsize=10,
    )
    page.insert_text((64, 252), "Methods", fontsize=12)
    page.insert_textbox(
        pymupdf.Rect(64, 268, 540, 410),
        "Two simulated acquisitions were analyzed with the qMRLab toolbox. "
        "Quantitative MRI T1 mapping and T2 mapping were compared using a fixed test protocol.",
        fontsize=10,
    )
    page.insert_text((64, 445), "Figure 1. Synthetic parameter-map tile.", fontsize=9)
    page.insert_image(pymupdf.Rect(64, 460, 240, 625), stream=_png_sample())

    page = document.new_page(width=612, height=792)
    page.insert_text((64, 68), "Results and conclusion", fontsize=15)
    page.insert_textbox(
        pymupdf.Rect(64, 95, 540, 250),
        "The simulated repeated measurements were stable in this fictional example. "
        "The results are illustrative and do not represent clinical evidence.",
        fontsize=10,
    )
    document.save(fixture)
    document.close()
    return fixture


def _report_path() -> Path:
    configured = os.environ.get("E2E_REPORT")
    if configured:
        return Path(configured)
    log_dir = Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs"))
    return log_dir / "e2e-report.json"


def _record(report: dict[str, Any], name: str, status: str, detail: Any = None) -> None:
    row: dict[str, Any] = {"name": name, "status": status}
    if detail is not None:
        row["detail"] = detail
    report["checks"].append(row)


def _check_status(
    report: dict[str, Any], name: str, response: Any, expected: int | tuple[int, ...]
) -> None:
    expected_values = (expected,) if isinstance(expected, int) else expected
    status = int(response.status_code)
    _record(report, name, "passed" if status in expected_values else "failed", {
        "status_code": status,
        "expected": list(expected_values),
    })
    if os.environ.get("E2E_SOFT_ASSERT") == "1" and status not in expected_values:
        # Soft mode: record the failure and keep going so one run surfaces every problem.
        return
    assert status in expected_values, f"{name}: got HTTP {status}, expected {expected_values}"


def _response_data(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, AttributeError):
        return {}


def _seed_papers() -> tuple[int, int]:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from paper_library.db import get_session
    from paper_library.models import Paper

    suffix = uuid.uuid4().hex[:10]
    with get_session(str(ROOT_DIR), os.environ.get("CONFIG_DIR", "config")) as session:
        main_paper = Paper(
            title=f"E2E qMRI fixture {suffix}",
            abstract="Fictional quantitative MRI data for an end-to-end test.",
            year=2026,
            journal_name="Magnetic Resonance in Medicine",
            is_in_domain=None,
            processing_status="done", review_outcome="unreviewed",
            source="e2e_fixture",
        )
        permission_paper = Paper(
            title=f"E2E permission fixture {suffix}",
            abstract="Fictional permission test record.",
            year=2026,
            journal_name="Magnetic Resonance in Medicine",
            processing_status="done", review_outcome="unreviewed",
            source="e2e_fixture",
        )
        session.add_all((main_paper, permission_paper))
        session.flush()
        return int(main_paper.id), int(permission_paper.id)


def _attach_browser_collector(page: Page, report: dict[str, Any], state: dict[str, str]) -> None:
    def on_console(message: Any) -> None:
        if message.type == "error":
            report["browser"]["console_errors"].append({
                "step": state.get("step", "unknown"),
                "text": message.text,
            })

    def on_page_error(error: Exception) -> None:
        report["browser"]["page_errors"].append({
            "step": state.get("step", "unknown"),
            "text": str(error),
        })

    def on_request_failed(request: Any) -> None:
        failure = request.failure or "request failed"
        report["browser"]["failed_requests"].append({
            "step": state.get("step", "unknown"),
            "method": request.method,
            "url": request.url,
            "failure": failure,
            "expected_navigation_abort": "ERR_ABORTED" in str(failure),
        })

    def on_response(response: Any) -> None:
        status = response.status
        if status >= 400 and status not in (401, 403):
            report["browser"]["failed_requests"].append({
                "step": state.get("step", "unknown"),
                "method": response.request.method,
                "url": response.url,
                "status_code": status,
                "expected_navigation_abort": False,
            })

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)
    page.on("response", on_response)


def _wait_for_paper(api_client: Any, report: dict[str, Any], paper_id: int, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = api_client.get(f"/api/papers/{paper_id}")
        _check_status(report, "poll uploaded paper extraction", response, 200)
        last = _response_data(response)
        if last.get("pdf_text_extracted"):
            return last
        time.sleep(1)
    raise AssertionError(f"PDF text extraction did not finish for paper {paper_id}: {last.get('processing_status')}")


def _run_domain_search(report: dict[str, Any], api_client: Any) -> None:
    if os.environ.get("E2E_SKIP_EXTERNAL_SEARCH", "").lower() in {"1", "true", "yes"}:
        _record(report, "external domain search", "skipped", "E2E_SKIP_EXTERNAL_SEARCH is set")
        return

    now_year = dt.datetime.now(dt.UTC).year
    first_year = now_year - 1
    output_dir = Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "domain-search-candidates.csv"
    csv_path.unlink(missing_ok=True)
    common = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "search_domain_papers.py"),
        "--years", f"{first_year}-{now_year}",
        "--source", "pubmed",
        "--max-per-year", "1",
        "--workspace", str(ROOT_DIR),
        "--output", str(csv_path),
    ]
    for label, extra in (("dry-run", ["--dry-run"]), ("auto-ingest", ["--auto-ingest"])):
        try:
            result = subprocess.run(
                common + extra,
                cwd=ROOT_DIR,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("E2E_SEARCH_TIMEOUT", "180")),
                check=False,
            )
            output = f"{result.stdout}\n{result.stderr}"
            external_error = any(
                marker in output.casefold()
                for marker in ("搜索失败", "获取失败", "批次获取失败", "未配置")
            )
            if result.returncode or external_error:
                reason = (
                    f"script exited with status {result.returncode}"
                    if result.returncode
                    else "the search script reported an external service/configuration failure"
                )
                report["external_dependency_failures"].append({
                    "step": f"domain search {label}",
                    "reason": reason,
                })
                _record(report, f"domain search {label}", "external_dependency_failure", {
                    "return_code": result.returncode,
                    "reason": reason,
                })
            else:
                _record(report, f"domain search {label}", "passed", "bounded PubMed run completed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["external_dependency_failures"].append({
                "step": f"domain search {label}",
                "reason": type(exc).__name__,
            })
            _record(report, f"domain search {label}", "external_dependency_failure", type(exc).__name__)

    if not csv_path.exists():
        _record(report, "Crossref metadata enrichment", "skipped", "search returned no candidate file")
        return

    doi: str | None = None
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                candidate = str(row.get("DOI") or "").strip()
                if candidate:
                    doi = candidate
                    break
    except (OSError, csv.Error):
        doi = None
    if not doi:
        _record(report, "Crossref metadata enrichment", "skipped", "search returned no DOI candidate")
        return

    try:
        response = api_client.post("/api/papers/batch-doi", json={"dois": [doi], "translate_zh": False}, timeout=20)
        payload = _response_data(response)
        rows = payload.get("results") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not rows or rows[0].get("status") == "error":
            detail = f"HTTP {response.status_code} or Crossref returned no metadata"
            report["external_dependency_failures"].append({
                "step": "Crossref metadata enrichment",
                "reason": detail,
            })
            _record(report, "Crossref metadata enrichment", "external_dependency_failure", detail)
        else:
            _record(report, "Crossref metadata enrichment", "passed", "one candidate was enriched through the DOI API")
    except Exception as exc:  # External network availability is not an application assertion.
        report["external_dependency_failures"].append({
            "step": "Crossref metadata enrichment",
            "reason": type(exc).__name__,
        })
        _record(report, "Crossref metadata enrichment", "external_dependency_failure", type(exc).__name__)


def _enrich_jcr(paper_id: int) -> None:
    sys.path.insert(0, str(ROOT_DIR / "src"))
    from paper_library.db import get_session
    from paper_library.journal_enrichment import JcrMatcher, enrich_paper_journal
    from paper_library.models import Paper

    with get_session(str(ROOT_DIR), os.environ.get("CONFIG_DIR", "config")) as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            raise AssertionError(f"E2E paper {paper_id} disappeared before journal enrichment")
        result = enrich_paper_journal(
            paper,
            session,
            JcrMatcher(workspace=ROOT_DIR).load(),
        )
        if result.get("status") != "matched":
            raise AssertionError(f"JCR journal match failed: {result}")


def _wait_for_publish(
    api_client: Any,
    report: dict[str, Any],
    record_id: int,
    timeout: int,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api_client.get("/api/drafts/outbox")
        _check_status(report, "poll publish outbox", response, 200)
        items = _response_data(response).get("items", [])
        for draft in items:
            for account in draft.get("accounts", []):
                if int(account.get("record_id", -1)) == record_id:
                    if account.get("status") in {"success", "failed", "cancelled"}:
                        return account
        time.sleep(2)
    return None


def test_live_e2e_flows() -> None:
    assert httpx is not None and sync_playwright is not None
    base_url = os.environ["E2E_BASE_URL"].rstrip("/")
    api_url = os.environ["E2E_API_URL"].rstrip("/")
    log_dir = Path(os.environ.get("E2E_LOG_DIR", "/tmp/paper-pulse-e2e-logs"))
    screen_dir = log_dir / "screens"
    screen_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": dt.datetime.now(dt.UTC).isoformat(),
        "base_url": base_url,
        "api_url": api_url,
        "checks": [],
        "external_dependency_failures": [],
        "blocked_flows": [],
        "screenshots": [],
        "browser": {"console_errors": [], "page_errors": [], "failed_requests": []},
    }
    clients: dict[str, Any] = {}
    contexts: dict[str, BrowserContext] = {}
    page_states: dict[str, dict[str, str]] = {}
    browser: Browser | None = None

    try:
        fixture = _make_sample_pdf()
        report["sample_pdf"] = str(fixture)
        paper_id, permission_paper_id = _seed_papers()
        report["fixture_paper_ids"] = [paper_id, permission_paper_id]

        with httpx.Client(base_url=api_url, timeout=30) as anonymous:
            for role, password in PASSWORDS.items():
                response = anonymous.post("/api/auth/login", json={"username": role, "password": password})
                _check_status(report, f"API login {role}", response, 200)
                payload = _response_data(response)
                assert payload.get("role") == role
                clients[role] = httpx.Client(
                    base_url=api_url,
                    timeout=60,
                    headers={"Authorization": f"Bearer {payload['access_token']}"},
                )

        for role, api_client in clients.items():
            for path in (
                "/api/papers?page=1&page_size=5",
                "/api/papers/entities",
                "/api/drafts?page=1&page_size=5",
                "/api/stats/drafts",
                "/api/authors/geo?scope=all",
            ):
                response = api_client.get(path)
                _check_status(report, f"{role} read {path.split('?')[0]}", response, 200)

        entity_response = clients["admin"].get("/api/papers/entities")
        _check_status(report, "read configured entities", entity_response, 200)
        entity_data = _response_data(entity_response)
        target_label = entity_data.get("target_label")
        assert target_label, "The runtime has no configured target entity"

        pdf_bytes = fixture.read_bytes()
        for role in ("reviewer", "limited"):
            response = clients[role].post(
                f"/api/papers/{permission_paper_id}/upload-pdf",
                files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
            )
            _check_status(report, f"{role} cannot upload PDFs", response, 403)

        for role in ("reviewer", "limited"):
            response = clients[role].patch(
                f"/api/papers/{paper_id}",
                json={"abstract_zh": f"E2E permission check {role}"},
            )
            _check_status(report, f"{role} cannot edit papers", response, 403)

        for role in ("viewer", "limited"):
            response = clients[role].post(
                f"/api/papers/{permission_paper_id}/review",
                json={"decision": "approve", "entity_label": target_label},
            )
            _check_status(report, f"{role} cannot review papers", response, 403)

        admin_upload = clients["admin"].post(
            f"/api/papers/{permission_paper_id}/upload-pdf",
            files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        )
        _check_status(report, "admin uploads PDF", admin_upload, 200)
        admin_review = clients["admin"].post(
            f"/api/papers/{permission_paper_id}/review",
            json={"decision": "approve", "entity_label": target_label},
        )
        _check_status(report, "admin approves paper", admin_review, 200)

        viewer_upload = clients["viewer"].post(
            f"/api/papers/{paper_id}/upload-pdf",
            files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        )
        _check_status(report, "viewer uploads PDF", viewer_upload, 200)
        paper_data = _wait_for_paper(clients["viewer"], report, paper_id)

        sys.path.insert(0, str(ROOT_DIR / "src"))
        from core.entity_detector import detect_entity
        from core.domain_config import load_domain
        from paper_library.db import get_session
        from paper_library.models import Paper

        # The detail API does not return full text; read what the upload extracted from the database.
        with get_session() as db_session:
            paper_data = dict(paper_data)
            paper_data["extracted_text"] = db_session.get(Paper, paper_id).extracted_text or ""
        assert paper_data["extracted_text"].strip(), "upload reported extraction but stored text is empty"

        recognized_entity, _confidence, _scores = detect_entity(paper_data.get("extracted_text") or "")
        _record(report, "entity recognition from extracted text", "passed" if recognized_entity == target_label else "failed", {
            "recognized_entity": recognized_entity,
            "target_label": target_label,
        })
        assert recognized_entity == target_label, f"expected detected entity {target_label!r}, got {recognized_entity!r}"
        extracted_text = str(paper_data.get("extracted_text") or "")
        domain = load_domain()
        is_in_domain = any(
            str(term).casefold() in extracted_text.casefold()
            for term in domain.get("relevance_keywords", [])
            if str(term).strip()
        )
        assert is_in_domain, "The generated paper text did not match the configured qMRI domain keywords"
        # Production path: entity detection runs through scripts/enrich_entities.py over the stored full text.
        import subprocess
        enrich = subprocess.run(
            [sys.executable, str(ROOT_DIR / "scripts" / "enrich_entities.py"), "--paper-id", str(paper_id), "--force"],
            cwd=ROOT_DIR, env={**os.environ, "PYTHONPATH": str(ROOT_DIR / "src")},
            capture_output=True, text=True, timeout=120,
        )
        _record(report, "run enrich_entities script", "passed" if enrich.returncode == 0 else "failed", {
            "returncode": enrich.returncode, "stderr_tail": enrich.stderr[-300:],
        })
        assert enrich.returncode == 0, enrich.stderr[-500:]
        detail_response = clients["viewer"].get(f"/api/papers/{paper_id}")
        _check_status(report, "verify extracted entity fields", detail_response, 200)
        detail = _response_data(detail_response)
        _record(report, "verify entity_label stored by enrich_entities", "passed" if detail.get("entity_label") == target_label else "failed", {
            "entity_label": detail.get("entity_label"), "domain_keywords_matched": is_in_domain,
        })
        assert detail.get("entity_label") == target_label

        _enrich_jcr(paper_id)
        jcr_response = clients["admin"].get(f"/api/papers/{paper_id}")
        _check_status(report, "read JCR-enriched paper", jcr_response, 200)
        jcr_data = _response_data(jcr_response)
        for field in ("impact_factor", "jcr_quartile", "cas_quartile"):
            assert jcr_data.get(field) not in (None, ""), f"JCR field {field} was not filled"
        _record(report, "JCR and partition enrichment", "passed", {
            "journal_name": jcr_data.get("journal_name"),
            "impact_factor": jcr_data.get("impact_factor"),
            "jcr_quartile": jcr_data.get("jcr_quartile"),
            "cas_quartile": jcr_data.get("cas_quartile"),
        })

        viewer_review = clients["viewer"].post(
            f"/api/papers/{paper_id}/review",
            json={"decision": "approve", "entity_label": target_label},
        )
        _check_status(report, "viewer cannot review papers", viewer_review, 403)
        reviewer_review = clients["reviewer"].post(
            f"/api/papers/{paper_id}/review",
            json={"decision": "approve", "note": "E2E 通过审核", "entity_label": target_label},
        )
        _check_status(report, "reviewer approves paper", reviewer_review, 200)
        ready_response = clients["viewer"].get(f"/api/papers/{paper_id}")
        _check_status(report, "verify paper review status", ready_response, 200)
        assert _response_data(ready_response).get("processing_status") == "done"
        assert _response_data(ready_response).get("review_outcome") == "approved"
        assert _response_data(ready_response).get("can_generate") is True

        drafts_before_response = clients["admin"].get("/api/drafts?page=1&page_size=1000")
        _check_status(report, "read drafts before generation", drafts_before_response, 200)
        drafts_before = {
            int(item["id"])
            for item in _response_data(drafts_before_response).get("items", [])
            if item.get("id") is not None
        }

        single_response = clients["viewer"].post(
            f"/api/papers/{paper_id}/generate-draft",
            json={"profile": "default", "themes": ["qMRI"], "model": "e2e-mock-model"},
        )
        _check_status(report, "single generate-draft", single_response, 200)
        single_draft_id = int(_response_data(single_response)["draft_id"])

        batch_response = clients["viewer"].post(
            "/api/papers/batch-generate-draft",
            json={"paper_ids": [paper_id], "profile": "default", "themes": ["qMRI"], "model": "e2e-mock-model"},
        )
        _check_status(report, "batch-generate-draft", batch_response, 200)
        batch_result = _response_data(batch_response)
        assert batch_result.get("success") == 1 and batch_result.get("failed") == 0
        drafts_after_response = clients["admin"].get("/api/drafts?page=1&page_size=1000")
        _check_status(report, "read generated drafts", drafts_after_response, 200)
        all_drafts = _response_data(drafts_after_response).get("items", [])
        new_draft_ids = [
            int(item["id"])
            for item in all_drafts
            if item.get("id") is not None and int(item["id"]) not in drafts_before
        ]
        batch_draft_ids = [draft_id for draft_id in new_draft_ids if draft_id != single_draft_id]
        assert batch_draft_ids, "Batch generation did not create a visible draft"
        report["draft_ids"] = [single_draft_id, *batch_draft_ids]
        for draft_id in report["draft_ids"]:
            draft_response = clients["admin"].get(f"/api/drafts/{draft_id}")
            _check_status(report, f"admin reads generated draft {draft_id}", draft_response, 200)
            draft_data = _response_data(draft_response)
            assert draft_data.get("title") and str(draft_data.get("content", "")).startswith("# ")

        # Editing drafts requires the reviewer or admin role.
        viewer_patch = clients["viewer"].patch(
            f"/api/drafts/{batch_draft_ids[0]}",
            json={"review_notes": "E2E role behavior probe"},
        )
        _check_status(report, "viewer cannot edit drafts", viewer_patch, 403)

        approve_response = clients["reviewer"].patch(
            f"/api/drafts/{single_draft_id}",
            json={"status": "approved", "review_notes": "E2E 审核通过"},
        )
        _check_status(report, "reviewer approves draft", approve_response, 200)
        reject_draft_id = batch_draft_ids[0]
        reject_response = clients["reviewer"].patch(
            f"/api/drafts/{reject_draft_id}",
            json={"status": "rejected", "review_notes": "E2E 审核驳回"},
        )
        _check_status(report, "reviewer rejects draft", reject_response, 200)
        for draft_id, wanted in ((single_draft_id, "approved"), (reject_draft_id, "rejected")):
            response = clients["admin"].get(f"/api/drafts/{draft_id}")
            _check_status(report, f"admin reads reviewed draft {draft_id}", response, 200)
            assert _response_data(response).get("status") == wanted

        for role in ("viewer", "limited"):
            response = clients[role].post(
                f"/api/drafts/{single_draft_id}/publish",
                json={"account_ids": []},
            )
            _check_status(report, f"{role} cannot publish drafts", response, 403)

        ding_response = clients["admin"].get("/api/auth/dingtalk/config")
        _check_status(report, "DingTalk config endpoint", ding_response, 200)
        assert _response_data(ding_response).get("enabled") is False
        _record(report, "DingTalk is disabled", "passed", "enabled=false")

        _run_domain_search(report, clients["admin"])

        # Exercise the actual web login and all requested page families per role.
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for role in PASSWORDS:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                contexts[role] = context
                page = context.new_page()
                state = {"step": f"{role} login"}
                page_states[role] = state
                _attach_browser_collector(page, report, state)
                login_response = page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=45000)
                assert login_response is None or login_response.status < 400
                if role == "admin":
                    page.screenshot(path=str(screen_dir / "login-no-dingtalk.png"), full_page=True)
                    report["screenshots"].append(str(screen_dir / "login-no-dingtalk.png"))
                    dingtalk_entry_count = page.get_by_text("钉钉登录", exact=False).count()
                    _record(report, "login page has no DingTalk entry", "passed" if not dingtalk_entry_count else "failed")
                    assert dingtalk_entry_count == 0
                page.get_by_label("用户名").fill(role)
                page.get_by_label("密码").fill(PASSWORDS[role])
                page.get_by_role("button", name="登录").click()
                page.wait_for_timeout(1000)
                assert urlparse(page.url).path != "/login", f"Browser login failed for {role}"
                for path in ("/papers", "/authors", "/stats", "/settings", "/workspace", "/settings/dingtalk"):
                    state["step"] = f"{role} page {path}"
                    response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=45000)
                    assert response is None or response.status < 400, f"{role} page {path} returned an error"
                    page.wait_for_timeout(450)
                    _record(report, f"{role} opens {path}", "passed")

            admin_page = contexts["admin"].pages[0]
            admin_state = page_states["admin"]
            for path, filename in (
                ("/authors", "authors.png"),
                ("/stats", "stats.png"),
                ("/settings", "settings.png"),
                ("/settings/dingtalk", "settings-dingtalk.png"),
                ("/workspace", "workspace.png"),
            ):
                admin_state["step"] = f"admin screenshot {path}"
                admin_page.goto(f"{base_url}{path}", wait_until="domcontentloaded", timeout=45000)
                admin_page.wait_for_timeout(900)
                if path == "/workspace":
                    admin_page.get_by_role("tab", name="已审核").click()
                    admin_page.wait_for_timeout(500)
                target = screen_dir / filename
                admin_page.screenshot(path=str(target), full_page=True)
                report["screenshots"].append(str(target))
                if path == "/workspace":
                    workbench_text = admin_page.locator("body").inner_text(timeout=10000)
                    draft_title = str(_response_data(clients["admin"].get(f"/api/drafts/{single_draft_id}")).get("title") or "")
                    assert draft_title and draft_title in workbench_text, "Generated draft was not visible in the workbench"
                    _record(report, "generated draft appears in workbench", "passed", draft_title)
                if path in {"/authors", "/stats", "/settings", "/settings/dingtalk"}:
                    report.setdefault("screenshot_page_checks", []).append(path)
            browser.close()
            browser = None

        test_account_name = f"e2e-{uuid.uuid4().hex[:12]}"
        account_response = clients["reviewer"].post(
            "/api/accounts",
            json={
                "channel": "csdn",
                "account_name": test_account_name,
                "credential_type": "cookie",
                "credential": f"session=synthetic-{uuid.uuid4().hex}",
            },
        )
        _check_status(report, "create E2E publishing account", account_response, 201)
        account_id = int(_response_data(account_response)["id"])

        publish_response = clients["reviewer"].post(
            f"/api/drafts/{single_draft_id}/publish",
            json={"account_ids": [account_id]},
        )
        _check_status(report, "enqueue E2E account publish", publish_response, 200)
        task = _response_data(publish_response).get("tasks", [])[0]
        published = _wait_for_publish(
            clients["admin"], report, int(task["record_id"]),
            int(os.environ.get("E2E_PUBLISH_TIMEOUT", "180")),
        )
        assert published and published.get("status") == "success", f"E2E account publish did not succeed: {published}"
        publish_dir = Path(os.environ.get("E2E_LOG_DIR", str(log_dir))).expanduser().resolve() / "published" / test_account_name
        output_files = sorted(publish_dir.glob(f"draft-{single_draft_id}-*.md"))
        assert len(output_files) == 1, f"Expected one fake publisher output under {publish_dir}, got {len(output_files)}"
        _record(report, "fake publisher output exists", "passed", str(output_files[0]))

        scheduled_at = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)).isoformat()
        schedule_response = clients["reviewer"].post(
            f"/api/drafts/{single_draft_id}/schedule",
            json={"account_ids": [account_id], "scheduled_at": scheduled_at},
        )
        _check_status(report, "create one-minute scheduled publish", schedule_response, 200)
        schedule_deadline = time.monotonic() + int(os.environ.get("E2E_SCHEDULE_TIMEOUT", "420"))
        scheduled_success = False
        while time.monotonic() < schedule_deadline:
            response = clients["admin"].get("/api/stats/publish")
            _check_status(report, "poll scheduled publish", response, 200)
            records = _response_data(response).get("recent_records", [])
            match = next((item for item in records if item.get("draft_id") == single_draft_id and item.get("channel") == "csdn" and item.get("account_name") == test_account_name and item.get("status") == "success" and item.get("published_at")), None)
            if match:
                success_count = sum(
                    1 for item in records
                    if item.get("draft_id") == single_draft_id
                    and item.get("channel") == "csdn"
                    and item.get("account_name") == test_account_name
                    and item.get("status") == "success"
                )
                if success_count >= 2:
                    scheduled_success = True
                    break
            time.sleep(3)
        assert scheduled_success, "The scheduler did not enqueue and complete the due publish"
        output_files = sorted(publish_dir.glob(f"draft-{single_draft_id}-*.md"))
        assert len(output_files) >= 2, f"Expected two fake publisher outputs under {publish_dir}, got {len(output_files)}"
        _record(report, "scheduled publish completed", "passed", {"output_files": [str(path) for path in output_files]})

        assert not report["browser"]["console_errors"], f"Browser console errors: {report['browser']['console_errors']}"
        assert not report["browser"]["page_errors"], f"Browser page errors: {report['browser']['page_errors']}"
        unexpected_failed_requests = [
            item for item in report["browser"]["failed_requests"]
            if not item.get("expected_navigation_abort")
        ]
        assert not unexpected_failed_requests, f"Browser failed requests: {unexpected_failed_requests}"
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        for context in contexts.values():
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass
        report["finished_at"] = dt.datetime.now(dt.UTC).isoformat()
        target = _report_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

