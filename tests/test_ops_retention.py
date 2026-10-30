"""Regression tests for temp-file hygiene and queue retention."""
from __future__ import annotations

import datetime as dt
import os

from paper_library.db import get_session
from paper_library.models import Job
from worker.queue import cleanup_terminal_jobs


def test_terminal_job_retention_only_removes_old_finished_rows():
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=45)
    with get_session() as session:
        session.add_all([
            Job(id="old-succeeded", kind="test", payload={}, status="succeeded", updated_at=old, created_at=old),
            Job(id="fresh-failed", kind="test", payload={}, status="failed", updated_at=dt.datetime.now(dt.UTC)),
            Job(id="old-queued", kind="test", payload={}, status="queued", updated_at=old, created_at=old),
        ])
        session.commit()

    assert cleanup_terminal_jobs(older_than_days=30) == 1
    with get_session() as session:
        assert session.get(Job, "old-succeeded") is None
        assert session.get(Job, "fresh-failed") is not None
        assert session.get(Job, "old-queued") is not None
    assert cleanup_terminal_jobs(older_than_days=0) == 0


def test_temp_download_filename_cannot_escape_temp_directory(monkeypatch):
    from api import temp_download

    temp_dir = temp_download.get_temp_dir()
    info = temp_download.create_temp_file(b"pdf", "../../evil name.pdf", "application/pdf")
    assert os.path.dirname(info["path"]) == str(temp_dir)
    assert "/" not in info["filename"]
    assert ".." not in info["path"]
    os.unlink(info["path"])
