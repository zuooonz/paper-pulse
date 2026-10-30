"""Tests for enrich_journal.py JCR matching script"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paper_library.models import Base, Paper, Journal, JournalMetric
from scripts.enrich_journal import (
    normalize_journal,
    upsert_journal_metric,
    main,
)


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database for testing"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.rollback()
    session.close()


def test_normalize_journal():
    """Test journal name normalization"""
    # Test basic normalization
    assert normalize_journal("  Nature  Communications  ") == "NATURE COMMUNICATIONS"
    # Test punctuation removal
    assert normalize_journal("CA: A Cancer Journal for Clinicians") == "CA A CANCER JOURNAL FOR CLINICIANS"
    # Test empty/None
    assert normalize_journal(None) == ""
    assert normalize_journal("") == ""
    # Test special characters
    assert normalize_journal("Journal-of-Biology & Medicine") == "JOURNAL-OF-BIOLOGY & MEDICINE"


def test_upsert_journal_metric_new(in_memory_db):
    """Test creating new JournalMetric"""
    journal = Journal(name="Test Journal", issn="1234-5678")
    in_memory_db.add(journal)
    in_memory_db.flush()

    metric = upsert_journal_metric(in_memory_db, journal, 2024, 5.2, "Q1")
    assert metric.id is not None
    assert metric.journal_id == journal.id
    assert metric.year == 2024
    assert metric.impact_factor == 5.2
    assert metric.jcr_quartile == "Q1"


def test_upsert_journal_metric_update(in_memory_db):
    """Test updating existing JournalMetric"""
    journal = Journal(name="Test Journal", issn="1234-5678")
    in_memory_db.add(journal)
    in_memory_db.flush()

    # First insert
    metric1 = upsert_journal_metric(in_memory_db, journal, 2024, 5.2, "Q1")
    # Update
    metric2 = upsert_journal_metric(in_memory_db, journal, 2024, 5.5, "Q1")
    assert metric1.id == metric2.id
    assert metric2.impact_factor == 5.5


def test_journal_matching(in_memory_db):
    """Test successful matching sets paper.journal_id"""
    # Add a paper without journal_id
    paper = Paper(
        title="Test qMRI Mapping Study",
        journal_name="NeuroImage",
        journal_id=None,
    )
    in_memory_db.add(paper)
    in_memory_db.commit()

    # Mock JCR data
    mock_jcr_dict = {
        "NEUROIMAGE": {
            "name": "NeuroImage",
            "issn": "1053-8119",
            "eissn": "1095-9572",
            "impact_factor": 7.4,
            "quartile": "Q1",
            "year": 2024,
        }
    }

    with patch("scripts.enrich_journal.load_jcr_data", return_value=mock_jcr_dict):
        with patch("sys.argv", ["enrich_journal.py", "--limit", "1"]):
            # Patch the database session to use our in-memory DB
            with patch("scripts.enrich_journal.get_session") as mock_get_session:
                mock_get_session.return_value.__enter__.return_value = in_memory_db
                main()

    # After running, paper should have journal_id set
    paper = in_memory_db.query(Paper).filter_by(title="Test qMRI Mapping Study").first()
    assert paper.journal_id is not None

    # Check journal was created
    journal = in_memory_db.query(Journal).filter_by(id=paper.journal_id).first()
    assert journal is not None
    assert journal.name == "NeuroImage"
    assert journal.issn == "1053-8119"

    # Check metric was created
    metric = in_memory_db.query(JournalMetric).filter_by(journal_id=journal.id).first()
    assert metric is not None
    assert metric.year == 2024
    assert metric.impact_factor == 7.4
    assert metric.jcr_quartile == "Q1"


def test_journal_no_match(in_memory_db):
    """Test when no match found, paper.journal_id remains None"""
    # Add a paper
    paper = Paper(
        title="Unknown Journal Paper",
        journal_name="NonExistentJournalXyz",
        journal_id=None,
    )
    in_memory_db.add(paper)
    in_memory_db.commit()

    # Mock JCR data with no match
    mock_jcr_dict = {
        "NEUROIMAGE": {
            "name": "NeuroImage",
            "issn": "1053-8119",
            "impact_factor": 7.4,
            "quartile": "Q1",
            "year": 2024,
        }
    }

    with patch("scripts.enrich_journal.load_jcr_data", return_value=mock_jcr_dict):
        with patch("sys.argv", ["enrich_journal.py", "--limit", "1"]):
            with patch("scripts.enrich_journal.get_session") as mock_get_session:
                mock_get_session.return_value.__enter__.return_value = in_memory_db
                main()

    # Check paper still has no journal_id
    paper = in_memory_db.query(Paper).filter_by(title="Unknown Journal Paper").first()
    assert paper.journal_id is None


def test_journal_dry_run(in_memory_db):
    """Test dry-run doesn't commit changes"""
    # Add a paper
    paper = Paper(
        title="Dry Run Test",
        journal_name="Nature",
        journal_id=None,
    )
    in_memory_db.add(paper)
    in_memory_db.commit()
    paper_id = paper.id

    mock_jcr_dict = {
        "NATURE": {
            "name": "Nature",
            "issn": "0028-0836",
            "impact_factor": 64.8,
            "quartile": "Q1",
            "year": 2024,
        }
    }

    with patch("scripts.enrich_journal.load_jcr_data", return_value=mock_jcr_dict):
        with patch("sys.argv", ["enrich_journal.py", "--dry-run", "--limit", "1"]):
            with patch("scripts.enrich_journal.get_session") as mock_get_session:
                mock_get_session.return_value.__enter__.return_value = in_memory_db
                main()

    # Check no changes - paper still has no journal_id
    paper = in_memory_db.query(Paper).filter_by(id=paper_id).first()
    assert paper.journal_id is None

    # No journal created
    journal_count = in_memory_db.query(Journal).count()
    assert journal_count == 0
