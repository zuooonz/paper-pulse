"""PDF pipeline tests: linking and extraction"""
import tempfile
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from paper_library.models import Paper, Base
from paper_library.crud import upsert_paper
from paper_library.pdf_pipeline import link_pdfs, extract_paper


def test_link_pdfs_dry_run():
    """Test linking with dry_run=True does not write to DB"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        # Create temp directory with a test PDF
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            (pdf_dir / "0001_100_2023_Test_Paper.pdf").write_bytes(b"")  # Empty PDF

            with factory() as session:
                # Add paper with internal_id=1
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper 1",
                    "processing_status": "pending",
                })
                session.commit()

                # Dry run link
                stats = link_pdfs(session, pdf_dir, dry_run=True)
                assert stats["linked"] == 1
                assert stats["already_set"] == 0
                assert stats["no_match"] == 0

                # Verify DB not changed
                paper = session.query(Paper).filter_by(internal_id=1).first()
                assert paper.pdf_storage_path is None

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_link_pdfs_writes_path():
    """Test linking with dry_run=False sets pdf_storage_path"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        # Create temp directory with a test PDF
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            pdf_file = pdf_dir / "0001_100_2023_Test_Paper.pdf"
            pdf_file.write_bytes(b"")

            with factory() as session:
                # Add paper with internal_id=1
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper 1",
                    "processing_status": "pending",
                })
                session.commit()

                # Actual linking
                stats = link_pdfs(session, pdf_dir, dry_run=False)
                assert stats["linked"] == 1
                assert stats["already_set"] == 0
                assert stats["no_match"] == 0

                # Verify DB updated
                paper = session.query(Paper).filter_by(internal_id=1).first()
                assert paper.pdf_storage_path == str(pdf_file.resolve()).replace("\\", "/")

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_link_pdfs_already_set():
    """Test already linked papers are counted as already_set"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            (pdf_dir / "0001_100_2023_Test_Paper.pdf").write_bytes(b"")

            with factory() as session:
                # Add paper with pdf_storage_path already set
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper 1",
                    "pdf_storage_path": "/already/set/path.pdf",
                    "processing_status": "pending",
                })
                session.commit()

                stats = link_pdfs(session, pdf_dir, dry_run=False)
                assert stats["linked"] == 0
                assert stats["already_set"] == 1
                assert stats["no_match"] == 0

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_link_pdfs_no_match():
    """Test PDF with no matching internal_id gets counted as no_match"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            (pdf_dir / "999_test.pdf").write_bytes(b"")  # internal_id=999 doesn't exist

            with factory() as session:
                # Add paper with different id
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper 1",
                    "processing_status": "pending",
                })
                session.commit()

                stats = link_pdfs(session, pdf_dir, dry_run=False)
                assert stats["linked"] == 0
                assert stats["already_set"] == 0
                assert stats["no_match"] == 1

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_link_pdfs_not_numeric_filename():
    """Test filenames not starting with digits_ are skipped"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            (pdf_dir / "abc_test.pdf").write_bytes(b"")  # Doesn't start with digits

            with factory() as session:
                stats = link_pdfs(session, pdf_dir, dry_run=False)
                assert stats["no_match"] == 1

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_extract_paper_missing_pdf():
    """Test extract_paper returns False when PDF file doesn't exist"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            image_dir = Path(tmpdir) / "images"

            with factory() as session:
                # Add paper with non-existent path
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper",
                    "pdf_storage_path": "/nonexistent/path.pdf",
                    "processing_status": "pending",
                })
                session.commit()

                paper = session.query(Paper).filter_by(internal_id=1).first()
                result = extract_paper(session, paper, image_dir, dry_run=False)

                # Should return False because file doesn't exist
                assert result is False
                # Status should still be pending
                assert paper.processing_status == "pending"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_extract_paper_empty_pdf_path():
    """Test extract_paper returns False when pdf_storage_path is empty"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            image_dir = Path(tmpdir) / "images"

            with factory() as session:
                # Add paper without pdf path
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper",
                    "processing_status": "pending",
                })
                session.commit()

                paper = session.query(Paper).filter_by(internal_id=1).first()
                result = extract_paper(session, paper, image_dir, dry_run=False)

                assert result is False
                assert paper.processing_status == "pending"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_extract_paper_success(monkeypatch):
    """Test successful extraction writes fields and sets processing to done."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            pdf_file = pdf_dir / "0001_100_2023_Test_Paper.pdf"
            pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")
            image_dir = Path(tmpdir) / "images"

            with factory() as session:
                # Add paper with path
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper",
                    "pdf_storage_path": str(pdf_file),
                    "processing_status": "pending",
                })
                session.commit()

                paper = session.query(Paper).filter_by(internal_id=1).first()

                # Monkeypatch the extractor to return known text
                def mock_extract(*args, **kwargs):
                    return ("hello text from PDF", ["image1.png", "image2.png"])

                # We need to patch it in paper_library.pdf_pipeline namespace because of direct import
                import paper_library.pdf_pipeline
                monkeypatch.setattr(paper_library.pdf_pipeline, "extract_text_and_images", mock_extract)

                result = extract_paper(session, paper, image_dir, dry_run=False)

                assert result is True
                assert paper.extracted_text == "hello text from PDF"
                assert paper.extracted_images == [{"path": "image1.png"}, {"path": "image2.png"}]
                assert paper.pdf_text_extracted is True
                assert paper.processing_status == "done"
                assert paper.processing_error is None

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_extract_paper_empty_text(monkeypatch):
    """Test when extraction returns empty text, status is set to error"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            pdf_file = pdf_dir / "0001_100_2023_Test_Paper.pdf"
            pdf_file.write_bytes(b"%PDF-1.4")
            image_dir = Path(tmpdir) / "images"

            with factory() as session:
                # Add paper with path
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper",
                    "pdf_storage_path": str(pdf_file),
                    "processing_status": "pending",
                })
                session.commit()

                paper = session.query(Paper).filter_by(internal_id=1).first()

                # Monkeypatch the extractor to return empty
                def mock_extract(*args, **kwargs):
                    return ("", [])

                # We need to patch it in paper_library.pdf_pipeline namespace because of direct import
                import paper_library.pdf_pipeline
                monkeypatch.setattr(paper_library.pdf_pipeline, "extract_text_and_images", mock_extract)

                result = extract_paper(session, paper, image_dir, dry_run=False)

                assert result is True
                assert paper.extracted_text is None or paper.extracted_text == ""
                assert paper.pdf_text_extracted is False
                assert paper.processing_status == "failed"
                assert paper.processing_error == "pdf_empty"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_extract_paper_dry_run_no_change(monkeypatch):
    """Test dry_run=True doesn't modify DB"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_dir = Path(tmpdir)
            pdf_file = pdf_dir / "0001_100_2023_Test_Paper.pdf"
            pdf_file.write_bytes(b"%PDF-1.4")
            image_dir = Path(tmpdir) / "images"

            with factory() as session:
                # Add paper with path
                upsert_paper(session, {
                    "internal_id": 1,
                    "doi": "10.1000/test1",
                    "title": "Test Paper",
                    "pdf_storage_path": str(pdf_file),
                    "processing_status": "pending",
                })
                session.commit()

                paper = session.query(Paper).filter_by(internal_id=1).first()
                original_status = paper.processing_status

                def mock_extract(*args, **kwargs):
                    return ("hello text", [])

                # We need to patch it in paper_library.pdf_pipeline namespace because of direct import
                import paper_library.pdf_pipeline
                monkeypatch.setattr(paper_library.pdf_pipeline, "extract_text_and_images", mock_extract)

                result = extract_paper(session, paper, image_dir, dry_run=True)

                assert result is True
                # DB should not change
                assert paper.extracted_text is None
                assert paper.processing_status == original_status

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass
