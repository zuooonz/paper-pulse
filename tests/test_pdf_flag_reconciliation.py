from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import inspect

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from api.routers.papers import common, listing
from paper_library.models import Base, Paper
from paper_library.paper_lifecycle import reconcile_pdf_flags
from worker import registry, scheduler


def test_reconcile_pdf_flags_repairs_missing_and_new_files(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'papers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    (tmp_path / "new.pdf").write_bytes(b"%PDF-new")
    with factory() as session:
        session.add_all([
            Paper(title="Missing file", pdf_storage_path="missing.pdf", has_pdf=True),
            Paper(title="New file", pdf_storage_path="new.pdf", has_pdf=False),
            Paper(title="No file", pdf_storage_path=None, has_pdf=False),
        ])
        session.commit()

    with factory() as session:
        assert reconcile_pdf_flags(session, tmp_path) == 2
        session.commit()
        flags = {
            paper.title: paper.has_pdf
            for paper in session.query(Paper).order_by(Paper.id).all()
        }

    assert flags == {"Missing file": False, "New file": True, "No file": False}
    engine.dispose()


def test_list_request_reads_pdf_flag_without_writing_or_mutating_paper(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'list.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        paper = Paper(title="Stale index", pdf_storage_path="missing.pdf", has_pdf=True)
        session.add(paper)
        session.commit()
        paper_id = paper.id

    updates = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record_updates(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)

    session_state = {}

    @contextmanager
    def session_context(**_kwargs):
        with factory() as session:
            session_state["paper"] = session.get(Paper, paper_id)
            yield session
            session_state["modified"] = session.is_modified(session_state["paper"])
            session.commit()

    monkeypatch.setattr(listing, "get_session", session_context)
    monkeypatch.setattr(listing.settings, "workspace", str(tmp_path))
    monkeypatch.setattr(common.settings, "workspace", str(tmp_path))
    monkeypatch.setattr(common, "load_domain", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(common, "generation_requires_target_entity", lambda *_args: False)
    monkeypatch.setattr(common, "get_target_label", lambda: None)
    common._paper_list_cache.clear()

    params = {}
    for name in inspect.signature(listing.list_papers).parameters:
        if name == "user":
            continue
        params[name] = {"page": 1, "page_size": 20, "sort_order": "desc"}.get(name)
    result = listing.list_papers(**params, user={"role": "admin"})
    facet_params = {
        name: None
        for name in inspect.signature(listing.get_filter_options).parameters
        if name != "user"
    }
    facets = listing.get_filter_options(**facet_params, user={"role": "admin"})

    assert result["items"][0]["id"] == paper_id
    assert result["items"][0]["has_pdf"] is True
    assert result["items"][0]["pdf_storage_path"] is None
    assert facets["has_pdf"] == [{"value": True, "count": 1}]
    assert updates == []
    assert session_state["paper"].has_pdf is True
    assert session_state["paper"].pdf_storage_path == "missing.pdf"
    assert session_state["modified"] is False
    common._paper_list_cache.clear()
    engine.dispose()


def test_pdf_flag_reconciliation_is_registered_and_scheduled(monkeypatch):
    assert "reconcile_pdf_flags" in registry.HANDLERS
    monkeypatch.setenv("PDF_FLAG_RECONCILE_HOURS", "24")
    enqueued = []
    monkeypatch.setattr(scheduler, "find_jobs", lambda **_kwargs: [])
    monkeypatch.setattr(scheduler, "enqueue", lambda *args, **kwargs: enqueued.append((args, kwargs)))

    scheduler._queue_pdf_flag_reconciliation("/workspace", "config")

    assert len(enqueued) == 1
    args, kwargs = enqueued[0]
    assert args[0] == "reconcile_pdf_flags"
    assert args[1] == {"workspace": "/workspace", "config_dir": "config"}

    enqueued.clear()
    monkeypatch.setenv("PDF_FLAG_RECONCILE_HOURS", "0")
    scheduler._queue_pdf_flag_reconciliation("/workspace", "config")
    assert enqueued == []

    monkeypatch.setenv("PDF_FLAG_RECONCILE_HOURS", "1")
    monkeypatch.setattr(scheduler, "find_jobs", lambda **_kwargs: [{
        "status": "succeeded",
        "created_at": datetime.now(UTC) - timedelta(hours=2),
    }])
    scheduler._queue_pdf_flag_reconciliation("/workspace", "config")
    assert len(enqueued) == 1
