"""Unit coverage for publish record transitions and cancellation priority."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from api.services.publishing import InvalidPublishTransition, transition_publish_record
from paper_library.models import DraftBase, PublishRecord


def _record(session, status):
    record = PublishRecord(draft_id=1, account_id=1, status=status)
    session.add(record)
    session.flush()
    return record.id


def test_publish_record_allows_only_the_declared_workflow_edges():
    engine = create_engine("sqlite:///:memory:")
    DraftBase.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            scheduled_id = _record(session, "scheduled")
            publishing_id = _record(session, "pending")
            failed_id = _record(session, "publishing")
            invalid_scheduled_id = _record(session, "scheduled")
            invalid_pending_id = _record(session, "pending")
            terminal_id = _record(session, "success")

            assert transition_publish_record(session, scheduled_id, "pending") is True
            assert transition_publish_record(session, publishing_id, "publishing") is True
            assert transition_publish_record(session, publishing_id, "success", url="file:///ok") is True
            assert transition_publish_record(session, failed_id, "failed", error="failed") is True
            assert transition_publish_record(session, invalid_scheduled_id, "publishing") is False
            assert transition_publish_record(session, invalid_pending_id, "success") is False
            assert transition_publish_record(session, terminal_id, "failed") is False
            with pytest.raises(InvalidPublishTransition):
                transition_publish_record(session, scheduled_id, "published")
    finally:
        engine.dispose()


def test_cancelled_publish_record_cannot_be_replaced_by_a_late_result():
    engine = create_engine("sqlite:///:memory:")
    DraftBase.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            record_ids = [_record(session, status) for status in ("pending", "scheduled", "publishing")]
            for record_id in record_ids:
                assert transition_publish_record(session, record_id, "cancelled") is True
                assert transition_publish_record(
                    session, record_id, "success", url="file:///late-result"
                ) is False
                assert transition_publish_record(
                    session, record_id, "failed", error="late error"
                ) is False
            for record_id in record_ids:
                record = session.get(PublishRecord, record_id)
                assert record.status == "cancelled"
                assert record.url is None
                assert record.error is None
    finally:
        engine.dispose()
