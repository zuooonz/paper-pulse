"""Focused regression coverage for the flow review fixes."""

import datetime as dt
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api.routers import papers as papers_router
from paper_library.author_ingest import normalize_author_names_for_paper, normalize_authors_for_paper
from paper_library.crud import upsert_paper
from paper_library.db import _database_init_lock, get_session
from paper_library.models import (
    ArticleDraft,
    Author,
    Base,
    ChannelAccount,
    Job,
    Paper,
    PaperAuthorMention,
    PublishRecord,
    paper_authors,
)
from paper_library.paper_lifecycle import (
    approve_paper,
    complete_processing,
    compute_metadata_complete,
    fail_processing,
    record_download_error,
    refresh_pdf_flag,
    reset_review,
    is_usable_for_generation,
    start_processing,
)
from worker import scheduler
from api.services.publishing import _queue_publish_task


def test_metadata_completeness_requires_bibliography_and_author():
    paper = Paper(
        title="Complete paper", doi="10.5555/complete", year=2025,
        journal_name="Example Journal", abstract="A complete abstract.",
        authors=[Author(name="Ada Lovelace")],
    )
    assert compute_metadata_complete(paper) is True
    paper.doi = None
    paper.pmid = "12345678"
    assert compute_metadata_complete(paper) is True
    paper.authors = []
    assert compute_metadata_complete(paper) is False


def test_pdf_flag_uses_actual_file_existence(tmp_path):
    pdf = tmp_path / "paper.pdf"
    paper = Paper(pdf_storage_path="paper.pdf")
    assert refresh_pdf_flag(paper, tmp_path) is False
    pdf.write_bytes(b"pdf")
    assert refresh_pdf_flag(paper, tmp_path) is True
    pdf.unlink()
    assert refresh_pdf_flag(paper, tmp_path) is False


def test_lifecycle_helpers_keep_download_failure_separate_from_processing_and_review():
    paper = Paper(processing_status="done", review_outcome="approved")
    record_download_error(paper)
    assert (paper.processing_status, paper.review_outcome) == ("done", "approved")
    assert paper.processing_error == "pdf_download_failed"
    fail_processing(paper, "pdf_extract_failed")
    assert (paper.processing_status, paper.review_outcome) == ("failed", "unreviewed")
    complete_processing(paper)
    assert (paper.processing_status, paper.review_outcome) == ("done", "unreviewed")
    approve_paper(paper, "reviewer", "verified")
    assert paper.reviewed_by == "reviewer"
    assert paper.review_note == "verified"
    reset_review(paper)
    assert paper.review_outcome == "unreviewed"
    assert paper.reviewed_by is None and paper.reviewed_at is None


def test_reprocessing_resets_the_previous_review_decision():
    paper = Paper(
        processing_status="done",
        review_outcome="approved",
        reviewed_by="reviewer",
        review_note="old decision",
    )
    start_processing(paper)
    assert paper.processing_status == "processing"
    assert paper.review_outcome == "unreviewed"
    assert paper.reviewed_by is None
    assert paper.review_note is None


def test_generation_gate_requires_done_approved_and_matching_entity():
    paper = Paper(
        processing_status="done", review_outcome="approved", entity_label="Target",
    )
    assert is_usable_for_generation(paper, True, "Target") is True
    assert is_usable_for_generation(paper, True, "Other") is False
    paper.review_outcome = "unreviewed"
    assert is_usable_for_generation(paper, True, "Target") is False
    paper.review_outcome = "excluded"
    assert is_usable_for_generation(paper, False, "Target") is False
    paper.review_outcome = "approved"
    paper.processing_status = "failed"
    assert is_usable_for_generation(paper, False, "Target") is False


def test_lifecycle_filters_and_sort_columns():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add_all([
                Paper(
                    title="Pending", processing_status="pending", review_outcome="unreviewed",
                    has_pdf=False, metadata_complete=False, entity_label="Target",
                ),
                Paper(
                    title="Processing", processing_status="processing", review_outcome="unreviewed",
                    has_pdf=True, metadata_complete=False, entity_label="Other",
                ),
                Paper(
                    title="Approved", processing_status="done", review_outcome="approved",
                    has_pdf=True, metadata_complete=True, entity_label="Target",
                ),
                Paper(
                    title="DoneUnreviewed", processing_status="done", review_outcome="unreviewed",
                    has_pdf=False, metadata_complete=False, entity_label="Target",
                ),
                Paper(
                    title="Excluded", processing_status="done", review_outcome="excluded",
                    has_pdf=False, metadata_complete=False, entity_label="Target",
                ),
            ])
            session.flush()
            processing_filter = papers_router._build_base_query(
                session, None, None, None, None,
                processing_status="pending,processing",
                has_pdf=False,
                metadata_complete=False,
                can_view_excluded=True,
            )
            assert [paper.title for paper in processing_filter.all()] == ["Pending"]
            review_filter = papers_router._build_base_query(
                session, None, None, None, None,
                review_outcome="unreviewed", can_view_excluded=True,
            )
            assert [paper.title for paper in review_filter.all()] == ["DoneUnreviewed"]
            admin_default = papers_router._build_base_query(
                session, None, None, None, None, can_view_excluded=True,
            )
            assert {paper.title for paper in admin_default.all()} == {
                "Pending", "Processing", "Approved", "DoneUnreviewed",
            }
            excluded_filter = papers_router._build_base_query(
                session, None, None, None, None,
                review_outcome="excluded", can_view_excluded=True,
            )
            assert [paper.title for paper in excluded_filter.all()] == ["Excluded"]
            limited = papers_router._build_base_query(
                session, None, None, None, None, restricted_role=True,
            )
            assert [paper.title for paper in limited.all()] == ["Approved"]
            from sqlalchemy import literal, select

            metric_columns = select(
                literal(None).label("impact_factor"),
                literal(None).label("cas_quartile"),
                literal(None).label("jcr_quartile"),
                literal(None).label("emerging_quartile"),
            ).subquery()
            for field in (
                "entity_label", "processing_status", "review_outcome", "has_pdf", "metadata_complete",
            ):
                sorted_query = papers_router._apply_sort(session.query(Paper), field, "asc", metric_columns)
                assert sorted_query.all()
    finally:
        engine.dispose()


def test_existing_paper_auto_entity_is_updated_but_manual_entity_is_preserved():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            auto = upsert_paper(session, {
                "doi": None,
                "title": "Auto entity",
                "entity_label": "Old Entity",
                "entity_source": "doi_metadata",
                "entity_confidence": "low",
            })
            manual = upsert_paper(session, {
                "doi": None,
                "title": "Manual entity",
                "entity_label": "Chosen Entity",
                "entity_source": "manual",
                "entity_confidence": "high",
            })
            empty = upsert_paper(session, {
                "doi": None,
                "title": "Empty entity",
            })
            session.flush()

            upsert_paper(session, {
                "doi": None,
                "title": auto.title,
                "entity_label": "New Entity",
                "entity_source": "doi_import",
                "entity_confidence": "high",
            })
            upsert_paper(session, {
                "doi": None,
                "title": manual.title,
                "entity_label": "Other Entity",
                "entity_source": "fulltext_keyword",
                "entity_confidence": "medium",
            })
            upsert_paper(session, {
                "doi": None,
                "title": empty.title,
                "entity_label": "Detected Entity",
                "entity_source": "doi_import",
                "entity_confidence": "medium",
            })
            session.flush()

            assert auto.entity_label == "New Entity"
            assert auto.entity_source == "doi_import"
            assert auto.entity_confidence == "high"
            assert manual.entity_label == "Chosen Entity"
            assert manual.entity_source == "manual"
            assert empty.entity_label == "Detected Entity"
    finally:
        engine.dispose()


def test_same_author_name_with_distinct_orcids_remains_distinct():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    metadata = {
        "author": [
            {"given": "Alex", "family": "Example", "ORCID": "0000-0001-1111-1111"},
            {"given": "Alex", "family": "Example", "ORCID": "0000-0002-2222-2222"},
        ]
    }
    try:
        with Session(engine) as session:
            paper = Paper(doi="10.5555/two-authors", title="Two authors")
            session.add(paper)
            session.flush()

            assert normalize_authors_for_paper(metadata, paper.id, session) == 2
            session.flush()
            linked = session.execute(
                select(paper_authors.c.author_id, paper_authors.c.author_order)
                .where(paper_authors.c.paper_id == paper.id)
                .order_by(paper_authors.c.author_order)
            ).all()
            assert len(linked) == 2
            assert linked[0].author_id != linked[1].author_id
            assert [row.author_order for row in linked] == [0, 1]
            assert session.scalar(select(func.count()).select_from(Author)) == 2

            normalize_authors_for_paper(metadata, paper.id, session)
            session.flush()
            assert session.scalar(select(func.count()).select_from(Author)) == 2
            assert session.scalar(select(func.count()).select_from(paper_authors)) == 2
    finally:
        engine.dispose()


def test_excel_author_names_create_ordered_relations_without_orcid():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            paper = Paper(
                doi=None,
                title="Excel author paper",
                original_data={"authors": "Ada Lovelace；Grace Hopper"},
            )
            session.add(paper)
            session.flush()

            assert normalize_author_names_for_paper(
                paper.original_data["authors"], paper.id, session
            ) == 2
            rows = session.execute(
                select(Author, paper_authors.c.author_order)
                .join(paper_authors, paper_authors.c.author_id == Author.id)
                .where(paper_authors.c.paper_id == paper.id)
                .order_by(paper_authors.c.author_order)
            ).all()
            assert [row[0].name for row in rows] == ["Ada Lovelace", "Grace Hopper"]
            assert [row[0].orcid for row in rows] == [None, None]
            assert [row[1] for row in rows] == [0, 1]
            assert session.scalar(select(func.count()).select_from(PaperAuthorMention)) == 2
    finally:
        engine.dispose()


def test_excel_import_ingests_original_author_string_without_doi_metadata(monkeypatch, tmp_path):
    import pandas as pd
    from scripts import import_excel_papers

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    columns = {"doi": ["DOI"], "title": ["Title"], "authors": ["Authors"]}
    monkeypatch.setattr(
        import_excel_papers.pd,
        "read_excel",
        lambda *_args, **_kwargs: pd.DataFrame([{
            "DOI": None,
            "Title": "Imported without DOI",
            "Authors": "Ada Lovelace; Grace Hopper",
        }]),
    )
    try:
        with Session(engine) as session:
            imported, created, updated, paper_ids = import_excel_papers.import_excel(
                tmp_path / "authors.xlsx",
                session,
                "authors.xlsx",
                no_crossref=True,
                columns=columns,
            )
            assert (imported, created, updated) == (1, 1, 0)
            paper = session.get(Paper, paper_ids[0])
            assert paper.doi_meta is None
            assert paper.processing_status == "pending"
            assert paper.review_outcome == "unreviewed"
            assert paper.metadata_complete is False
            assert paper.original_data["authors"] == "Ada Lovelace; Grace Hopper"
            assert session.scalar(select(func.count()).select_from(paper_authors).where(
                paper_authors.c.paper_id == paper.id
            )) == 2
    finally:
        engine.dispose()


def test_file_sqlite_uri_uses_resolved_database_path_for_init_lock(tmp_path):
    database_path = tmp_path / "uri-database.db"
    database_url = f"sqlite:///file:{database_path}?mode=rwc&uri=true"
    lock_path = tmp_path / "uri-database.db.init.lock"

    with _database_init_lock(database_url, str(tmp_path), "config"):
        assert lock_path.exists()


def test_scheduler_reconciles_stuck_publishing_from_terminal_job(monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "false")
    monkeypatch.setattr(scheduler, "PUBLISH_STUCK_MINUTES", 1)
    old_time = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)

    with get_session() as session:
        draft = ArticleDraft(draft_type="review", title="Recovery", content="Body")
        session.add(draft)
        session.flush()
        records = [
            PublishRecord(
                draft_id=draft.id,
                channel="test",
                account_name=f"account-{index}",
                status="publishing",
                created_at=old_time,
                updated_at=old_time,
            )
            for index in range(3)
        ]
        session.add_all(records)
        session.flush()
        session.add_all([
            Job(
                id="publish-success",
                kind="publish_draft",
                payload={"record_id": records[0].id},
                status="succeeded",
                priority=0,
                run_after=old_time,
                attempts=1,
                max_attempts=3,
                result={"status": "success", "url": "https://example.invalid/published"},
                created_at=old_time,
                updated_at=old_time,
                dedupe_key=f"publish-draft-record:{records[0].id}",
            ),
            Job(
                id="publish-failed",
                kind="publish_draft",
                payload={"record_id": records[1].id},
                status="failed",
                priority=0,
                run_after=old_time,
                attempts=3,
                max_attempts=3,
                error="publisher failed",
                created_at=old_time,
                updated_at=old_time,
                dedupe_key=f"publish-draft-record:{records[1].id}",
            ),
        ])
        record_ids = [record.id for record in records]

    assert scheduler._reconcile_pending_publishes(".", "config") == 3

    with get_session() as session:
        success, failed, missing_job = [session.get(PublishRecord, record_id) for record_id in record_ids]
        assert (success.status, success.url, success.error) == (
            "success", "https://example.invalid/published", None,
        )
        assert (failed.status, failed.error) == ("failed", "publisher failed")
        assert (missing_job.status, missing_job.error) == (
            "failed", "发布记录超时且对应任务不存在",
        )


def test_publish_idempotency_reuses_record_then_allows_key_after_24_hours():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            draft = ArticleDraft(draft_type="review", title="Idempotency", content="Body")
            account_row = ChannelAccount(
                channel="example", account_name="test account", credential_type="cookie", is_active=True
            )
            session.add_all([draft, account_row])
            session.flush()
            account = {
                "id": account_row.id,
                "channel": account_row.channel,
                "account_name": account_row.account_name,
            }
            draft_id = draft.id
            session.commit()
            next_job_number = 0

            def fake_enqueue(kind, payload, *, dedupe_key, session, inline):
                nonlocal next_job_number
                existing = session.query(Job).filter(Job.dedupe_key == dedupe_key).first()
                if existing:
                    return existing.id
                next_job_number += 1
                job_id = f"idempotency-job-{next_job_number}"
                now = dt.datetime.now(dt.UTC)
                session.add(Job(
                    id=job_id,
                    kind=kind,
                    payload=payload,
                    status="queued",
                    priority=0,
                    run_after=now,
                    attempts=0,
                    max_attempts=3,
                    created_at=now,
                    updated_at=now,
                    dedupe_key=dedupe_key,
                ))
                session.flush()
                return job_id

            first = _queue_publish_task(
                session, fake_enqueue, draft_id=draft_id, account=account,
                workspace=".", config_dir="config", idempotency_key="same-key",
            )
            repeated = _queue_publish_task(
                session, fake_enqueue, draft_id=draft_id, account=account,
                workspace=".", config_dir="config", idempotency_key="same-key",
            )
            assert repeated["record_id"] == first["record_id"]
            assert repeated["job_id"] == first["job_id"]

            old_record = session.get(PublishRecord, first["record_id"])
            old_record.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=25)
            expired_repeat = _queue_publish_task(
                session, fake_enqueue, draft_id=draft_id, account=account,
                workspace=".", config_dir="config", idempotency_key="same-key",
            )
            assert expired_repeat["record_id"] != first["record_id"]
            assert old_record.idempotency_key is None
            session.commit()

            assert session.query(PublishRecord).count() == 2
            assert session.query(Job).filter(Job.kind == "publish_draft").count() == 2
    finally:
        engine.dispose()


def test_publish_record_writeback_errors_are_propagated(monkeypatch):
    @contextmanager
    def failing_session(*_args, **_kwargs):
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr("api.services.publishing.get_session", failing_session)
    from api.services import publishing

    with pytest.raises(RuntimeError, match="database unavailable"):
        with publishing.get_session() as session:
            publishing.transition_publish_record(session, 10, "success")


def test_processing_status_filter_rejects_unknown_values():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session, pytest.raises(HTTPException) as exc_info:
            papers_router._build_base_query(
                session,
                None,
                None,
                None,
                None,
                processing_status="not_a_status",
            )
        assert exc_info.value.status_code == 422
    finally:
        engine.dispose()
