"""测试从合成 DB 数据运行批量生成脚本"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Add src to path
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paper_library.models import ArticleDraft, Base, Paper, draft_papers
from paper_library.crud import upsert_paper
from core import entity_detector

import paper_library.db


def _script_args(**overrides):
    values = {
        "limit": 1,
        "paper_id": None,
        "paper_ids": "",
        "internal_ids": "",
        "dry_run": False,
        "workspace": str(ROOT),
        "config_dir": "config",
        "profile": "default",
        "themes": "",
        "batch_size": 10,
        "status_filter": "not_generated",
        "force_regen": False,
        "save_files": False,
        "word": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _generation_domain_config():
    # These tests cover generation mechanics. If no target is configured, opt
    # out of entity scoping so the synthetic papers can still reach that path.
    return {
        "generation": {
            "require_target_entity": bool(entity_detector.get_target_label()),
        },
    }


def _test_databases(tmp_path, monkeypatch):
    app_path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{app_path}")
    paper_library.db.reset_engines()

    engine = create_engine(f"sqlite:///{app_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return (
        engine,
        sessionmaker(bind=engine),
        engine,
        sessionmaker(bind=engine),
    )


def test_generate_dry_run(tmp_path, monkeypatch):
    """测试 --dry-run 不写 DB、不写文件"""
    engine, factory, drafts_engine, drafts_factory = _test_databases(
        tmp_path, monkeypatch
    )

    try:
        with factory() as session:
            upsert_paper(session, {
                "doi": "10.1000/test-dry-run",
                "title": "Test Dry Run Paper",
                "year": 2024,
                "journal_name": "Test Journal",
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "not_generated",
                "entity_label": entity_detector.get_target_label(),
            })
            session.commit()

        # Import after setting DATABASE_URL so it uses our test DB
        from scripts import generate_from_db
        from core.config_loader import ConfigLoader

        # Use synthetic config as well as the synthetic paper database.
        with mock.patch("argparse.ArgumentParser.parse_args") as mock_parse_args, \
             mock.patch.object(ConfigLoader, "load_all") as mock_load_all, \
             mock.patch.object(
                 generate_from_db,
                 "get_target_label",
                 return_value=entity_detector.get_target_label(),
             ), \
             mock.patch("core.crossref_api.fetch_citation_by_doi", return_value=""):
            mock_parse_args.return_value = _script_args(
                dry_run=True,
                workspace=str(tmp_path),
            )
            mock_load_all.return_value = {
                "runtime": {"provider": {"base_url": "http://localhost", "api_key": "test"}},
                "models": {"default_model": "test-model"},
                "prompts": {"system_prompt": "Test prompt", "article_structure": []},
                "domain": _generation_domain_config(),
            }
            # Should run without errors
            generate_from_db.main()

        # Verify DB unchanged - article_status still not_generated
        with factory() as session:
            paper = session.query(Paper).first()
            assert paper.article_status == "not_generated"
        with drafts_factory() as session:
            draft_count = session.query(ArticleDraft).count()
            assert draft_count == 0

    finally:
        engine.dispose()
        paper_library.db.reset_engines()


def test_generate_writes_draft(tmp_path, monkeypatch):
    """测试生成后 ArticleDraft 记录被写入 DB 且 output 文件存在"""
    engine, factory, drafts_engine, drafts_factory = _test_databases(
        tmp_path, monkeypatch
    )

    try:
        # Insert test paper
        with factory() as session:
            upsert_paper(session, {
                "doi": "10.1000/test-generate",
                "title": "Test Generation Paper",
                "year": 2024,
                "journal_name": "Test Journal",
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "not_generated",
                "extracted_text": "This is the extracted full text of the paper.",
                "entity_label": entity_detector.get_target_label(),
            })
            session.commit()

        # Import and patch
        from scripts import generate_from_db
        from core.llm_client import OpenAICompatibleClient

        with mock.patch.object(OpenAICompatibleClient, "chat") as mock_chat, \
             mock.patch.object(
                 generate_from_db,
                 "get_target_label",
                 return_value=entity_detector.get_target_label(),
             ), \
             mock.patch("core.crossref_api.fetch_citation_by_doi", return_value=""):
            mock_chat.return_value = "# Test Generated Content\n\nThis is a generated test paragraph."

            with mock.patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
                mock_parse_args.return_value = _script_args(
                    workspace=str(tmp_path),
                    config_dir=str(ROOT / "config"),
                    profile="test_profile",
                    themes="临床,测试",
                    save_files=True,
                )
                # Also mock ConfigLoader to avoid loading real configs
                with mock.patch("core.config_loader.ConfigLoader.load_all") as mock_load_all, \
                     mock.patch("core.config_loader.ConfigLoader.load_profile", return_value={}):
                    mock_load_all.return_value = {
                        "runtime": {
                            "provider": {
                                "base_url": "http://localhost:8080/v1",
                                "api_key": "test-key",
                            }
                        },
                        "models": {
                            "default_model": "test-model",
                        },
                        "prompts": {
                            "system_prompt": "You are a helpful editor.",
                            "article_structure": [],
                        },
                        "domain": _generation_domain_config(),
                    }
                    generate_from_db.main()

        # Verify ArticleDraft created
        with drafts_factory() as session:
            draft_count = session.query(ArticleDraft).count()
            assert draft_count == 1
            draft = session.query(ArticleDraft).first()
            assert draft.title == "Test Generated Content"
            assert "# Test Generated Content" in draft.content
            assert draft.draft_type == "single_daily"
            assert draft.status == "draft"
            assert draft.meta["paper_id"] is not None
            assert draft.meta["model"] == "test-model"
            assert draft.meta["profile"] == "test_profile"
            linked_paper_ids = session.scalars(
                select(draft_papers.c.paper_id).where(
                    draft_papers.c.draft_id == draft.id
                )
            ).all()
            assert len(linked_paper_ids) == 1

        with factory() as session:
            linked_paper = session.get(Paper, linked_paper_ids[0])
            assert linked_paper.doi == "10.1000/test-generate"

        # Verify output file created
        output_dir = tmp_path / "output"
        assert output_dir.exists()
        md_files = list(output_dir.glob("*.md"))
        assert len(md_files) == 1
        assert "test-generation" in md_files[0].name.lower()
        content = md_files[0].read_text(encoding="utf-8")
        assert "# Test Generated Content" in content

    finally:
        engine.dispose()
        drafts_engine.dispose()
        paper_library.db.reset_engines()


def test_generate_updates_article_status(tmp_path, monkeypatch):
    """测试生成后 paper.article_status 更新为 generated"""
    engine, factory, drafts_engine, drafts_factory = _test_databases(
        tmp_path, monkeypatch
    )

    try:
        # Insert test paper
        with factory() as session:
            paper = upsert_paper(session, {
                "doi": "10.1000/test-status-update",
                "title": "Test Status Update",
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "not_generated",
                "extracted_text": "Test text",
                "entity_label": entity_detector.get_target_label(),
            })
            paper_id = paper.id
            session.commit()

        # Import and patch
        from scripts import generate_from_db
        from core.llm_client import OpenAICompatibleClient
        from core.config_loader import ConfigLoader

        with mock.patch.object(OpenAICompatibleClient, "chat") as mock_chat, \
             mock.patch.object(
                 generate_from_db,
                 "get_target_label",
                 return_value=entity_detector.get_target_label(),
             ), \
             mock.patch("core.crossref_api.fetch_citation_by_doi", return_value=""):
            mock_chat.return_value = "Generated content."

            with mock.patch("argparse.ArgumentParser.parse_args") as mock_parse_args:
                mock_parse_args.return_value = _script_args(
                    workspace=str(tmp_path),
                    config_dir="config",
                )
                with mock.patch.object(ConfigLoader, "load_all") as mock_load_all:
                    mock_load_all.return_value = {
                        "runtime": {
                            "provider": {
                                "base_url": "http://localhost:8080/v1",
                                "api_key": "test-key",
                            }
                        },
                        "models": {
                            "default_model": "test-model",
                        },
                        "prompts": {
                            "system_prompt": "You are an editor.",
                        },
                        "domain": _generation_domain_config(),
                    }
                    generate_from_db.main()

        # Verify article_status updated
        with factory() as session:
            paper = session.get(Paper, paper_id)
            assert paper.article_status == "generated"
        with drafts_factory() as session:
            assert session.query(ArticleDraft).count() == 1

    finally:
        engine.dispose()
        drafts_engine.dispose()
        paper_library.db.reset_engines()


def test_generate_collection_writes_draft(tmp_path, monkeypatch):
    """合集模式通过当前论文库 session 写入草稿和论文关联。"""
    engine, factory, drafts_engine, drafts_factory = _test_databases(
        tmp_path, monkeypatch
    )

    try:
        with factory() as session:
            paper_ids = []
            for index in range(2):
                paper = upsert_paper(session, {
                    "doi": f"10.1000/test-collection-{index}",
                    "title": f"Test Collection Paper {index}",
                    "year": 2024 + index,
                    "journal_name": "Test Journal",
                    "abstract": f"Summary for paper {index}.",
                    "processing_status": "done",
                "review_outcome": "approved",
                    "article_status": "not_generated",
                    "entity_label": entity_detector.get_target_label(),
                })
                paper_ids.append(paper.id)
            session.commit()

        from scripts import generate_from_db
        from core.config_loader import ConfigLoader
        from core.llm_client import OpenAICompatibleClient

        with mock.patch("argparse.ArgumentParser.parse_args") as mock_parse_args, \
             mock.patch.object(ConfigLoader, "load_all") as mock_load_all, \
             mock.patch.object(OpenAICompatibleClient, "chat") as mock_chat, \
             mock.patch.object(
                 generate_from_db,
                 "get_target_label",
                 return_value=entity_detector.get_target_label(),
             ), \
             mock.patch("core.crossref_api.fetch_citation_by_doi", return_value=""):
            mock_parse_args.return_value = _script_args(
                limit=2,
                workspace=str(tmp_path),
                config_dir=str(ROOT / "config"),
                profile="collection",
            )
            mock_load_all.return_value = {
                "runtime": {"provider": {"base_url": "http://localhost", "api_key": "test"}},
                "models": {"default_model": "test-model"},
                "prompts": {"system_prompt": "Test prompt", "review_structure": []},
                "domain": {
                    "name": "Test domain",
                    **_generation_domain_config(),
                },
            }
            mock_chat.return_value = "# Test Collection\n\nCollection content."

            generate_from_db.main()

        with drafts_factory() as session:
            draft = session.query(ArticleDraft).one()
            assert draft.draft_type == "collection"
            assert draft.title == "Test Collection"
            assert draft.meta["paper_ids"] == paper_ids
            linked_paper_ids = session.scalars(
                select(draft_papers.c.paper_id).where(
                    draft_papers.c.draft_id == draft.id
                )
            ).all()
            assert set(linked_paper_ids) == set(paper_ids)

        with factory() as session:
            assert {
                paper.article_status
                for paper in session.query(Paper).filter(Paper.id.in_(paper_ids)).all()
            } == {"generated"}

    finally:
        engine.dispose()
        drafts_engine.dispose()
        paper_library.db.reset_engines()


def test_paper_to_rec_mapping():
    """测试 paper 到 rec 的字段映射"""
    from scripts import generate_from_db
    paper_to_rec = generate_from_db.paper_to_rec

    # Create a paper object
    paper = Paper(
        id=123,
        internal_id=456,
        doi="10.1000/test",
        title="Test Title",
        journal_name="Test Journal",
        year=2024,
    )

    with mock.patch.object(generate_from_db, "get_target_label", return_value=""), \
         mock.patch("core.crossref_api.fetch_citation_by_doi", return_value=""):
        rec = paper_to_rec(paper)
    assert rec["论文编号"] == "456"
    assert rec["API权威标题"] == "Test Title"
    assert rec["英文标题"] == "Test Title"
    assert rec["API权威期刊"] == "Test Journal"
    assert rec["期刊名称"] == "Test Journal"
    assert rec["API发表日期"] == "2024"
    assert rec["DOI"] == "10.1000/test"

    # Fallback to id when internal_id None
    paper2 = Paper(id=789, internal_id=None, title="No internal id")
    with mock.patch.object(generate_from_db, "get_target_label", return_value=""):
        rec2 = paper_to_rec(paper2)
    assert rec2["论文编号"] == "789"


def test_slugify_title():
    """测试标题生成 slug"""
    from scripts.generate_from_db import slugify_title

    # Basic case
    slug = slugify_title("Synthetic qMRI parameter mapping benchmark")
    assert "synthetic-qmri" in slug
    assert len(slug) <= 30

    # With special chars
    slug = slugify_title("Test: A qMRI Study!")
    assert "test-a-qmri-study" in slug
    assert ":" not in slug
    assert "!" not in slug

    # Empty title
    assert slugify_title("") == "untitled"

    # Longer than 30 chars
    long_title = "a" * 50
    slug = slugify_title(long_title)
    assert len(slug) == 30
