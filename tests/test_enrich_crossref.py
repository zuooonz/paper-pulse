"""Tests for enrich_crossref.py script"""

import os
import sys
import subprocess

from sqlalchemy import select

from paper_library.db import init_db, get_session
from paper_library.models import Paper
from paper_library.crud import upsert_paper


def test_enrich_dry_run(tmp_path):
    """--dry-run 不应该修改数据库"""
    # 语法检查
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util; spec=importlib.util.spec_from_file_location('m','scripts/enrich_crossref.py'); m=importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(m); print('OK')",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "OK" in result.stdout

    # dry-run 执行检查（使用现有 DB，只读不写）
    result = subprocess.run(
        [
            sys.executable,
            "scripts/enrich_crossref.py",
            "--dry-run",
            "--limit",
            "2",
        ],
        capture_output=True,
        text=True,
        # Use a private database so the result does not depend on shared test DBs.
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'dry-run.db'}"},
    )
    assert result.returncode == 0
    assert "计划处理" in result.stdout
    assert "dry-run" in result.stdout


def test_enrich_updates_fields(tmp_path, monkeypatch):
    """mock API 返回数据验证字段被正确更新"""
    # 使用临时文件 SQLite（in-memory 因每连接新建 DB 不适用）
    db_file = tmp_path / "test.db"
    original_database_url = os.environ.get("DATABASE_URL")
    # 重置全局状态以使用新数据库 - get current values from module before changing
    import paper_library.db
    original_engine = paper_library.db._engine
    original_session_factory = paper_library.db._session_factory
    paper_library.db._engine = None
    paper_library.db._session_factory = None
    # Construct proper SQLite URL with absolute path (convert to forward slashes)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.absolute().as_posix()}"

    try:
        init_db()

        # 插入测试数据：有 DOI 但没有 doi_meta
        test_paper_data = {
            "title": "Test qMRI Mapping Study",
            "doi": "10.5555/example.qmri-crossref-2023",
            "abstract": None,
            "year": None,
            "journal_name": None,
            "doi_meta": None,
            "pdf_storage_path": "data/pdf/fallback.pdf",
            "processing_status": "pending",
            "processing_error": "Crossref 未匹配，已用 PDF 元数据入库",
        }

        with get_session() as session:
            paper = upsert_paper(session, test_paper_data)
            session.commit()
            paper_id = paper.id

        # mock crossref API
        mock_result = {
            "doi": "10.5555/example.qmri-crossref-2023",
            "title": "Test qMRI Mapping Study",
            "journal": {"title": "NeuroImage"},
            "year": 2023,
            "abstract": "This is a test abstract about quantitative MRI.",
            "raw": {"some": "raw", "data": "here"},
            "authors": [],
        }

        def mock_fetch(*args, **kwargs):
            return mock_result

        import core.crossref_api
        monkeypatch.setattr(core.crossref_api, "fetch_metadata_by_doi", mock_fetch)

        # 导入并运行 enrichment
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "enrich_crossref", "scripts/enrich_crossref.py"
        )
        assert spec is not None and spec.loader is not None
        enrich_module = importlib.util.module_from_spec(spec)
        sys.modules["enrich_crossref"] = enrich_module
        spec.loader.exec_module(enrich_module)

        # 模拟命令行参数
        original_argv = sys.argv
        sys.argv = ["enrich_crossref.py", "--paper-id", str(paper_id)]
        try:
            enrich_module.main()
        finally:
            sys.argv = original_argv

        # 验证结果
        with get_session() as session:
            stmt = select(Paper).where(Paper.id == paper_id)
            paper = session.scalar(stmt)
            assert paper is not None
            assert paper.doi_meta == mock_result["raw"]
            assert paper.abstract == mock_result["abstract"]
            assert paper.year == mock_result["year"]
            assert paper.journal_name == mock_result["journal"]["title"]
            assert paper.processing_status == "done"
            assert paper.processing_error is None

    finally:
        # 恢复全局状态
        paper_library.db._engine = original_engine
        paper_library.db._session_factory = original_session_factory
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)


def test_enrich_handles_api_failure(tmp_path, monkeypatch):
    """API 失败时不应该崩溃，doi_meta 保持 None"""
    # 使用临时文件 SQLite
    db_file = tmp_path / "test_fail.db"
    original_database_url = os.environ.get("DATABASE_URL")
    # 重置全局状态以使用新数据库 - get current values from module before changing
    import paper_library.db
    original_engine = paper_library.db._engine
    original_session_factory = paper_library.db._session_factory
    paper_library.db._engine = None
    paper_library.db._session_factory = None
    # Construct proper SQLite URL with absolute path (convert to forward slashes)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.absolute().as_posix()}"

    try:
        init_db()

        # 插入测试数据
        test_paper_data = {
            "title": "Test Paper with Bad DOI",
            "doi": "10.1000/invalid.doi",
            "abstract": None,
            "doi_meta": None,
            "processing_status": "pending",
            "processing_error": "Crossref 未匹配，已用 PDF 元数据入库",
        }

        with get_session() as session:
            paper = upsert_paper(session, test_paper_data)
            session.commit()
            paper_id = paper.id

        # mock 返回 Crossref 错误（API 失败）
        def mock_fetch(*args, **kwargs):
            return {"_error": "unavailable"}

        import core.crossref_api
        monkeypatch.setattr(core.crossref_api, "fetch_metadata_by_doi", mock_fetch)

        # 导入并运行
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "enrich_crossref", "scripts/enrich_crossref.py"
        )
        assert spec is not None and spec.loader is not None
        enrich_module = importlib.util.module_from_spec(spec)
        sys.modules["enrich_crossref"] = enrich_module
        spec.loader.exec_module(enrich_module)

        original_argv = sys.argv
        sys.argv = ["enrich_crossref.py", "--paper-id", str(paper_id)]
        try:
            enrich_module.main()
        finally:
            sys.argv = original_argv

        # 验证：doi_meta 仍然是 None，记录未被修改
        with get_session() as session:
            stmt = select(Paper).where(Paper.id == paper_id)
            paper = session.scalar(stmt)
            assert paper is not None
            assert paper.doi_meta is None
            assert paper.processing_status == "pending"
            assert paper.processing_error == "Crossref 未匹配，已用 PDF 元数据入库"

    finally:
        # 恢复全局状态
        paper_library.db._engine = original_engine
        paper_library.db._session_factory = original_session_factory
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)


def test_enrich_does_not_overwrite_existing_values(tmp_path, monkeypatch):
    """已有字段值不应该被覆盖"""
    # 使用临时文件 SQLite
    db_file = tmp_path / "test_keep.db"
    original_database_url = os.environ.get("DATABASE_URL")
    # 重置全局状态以使用新数据库 - get current values from module before changing
    import paper_library.db
    original_engine = paper_library.db._engine
    original_session_factory = paper_library.db._session_factory
    paper_library.db._engine = None
    paper_library.db._session_factory = None
    # Construct proper SQLite URL with absolute path (convert to forward slashes)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.absolute().as_posix()}"

    try:
        init_db()

        # 已有值：abstract=existing, year=2020
        test_paper_data = {
            "title": "Test Paper Existing Values",
            "doi": "10.5555/example.crossref-update",
            "abstract": "Existing abstract that should not be overwritten",
            "year": 2020,
            "journal_name": "Existing Journal",
            "doi_meta": None,
        }

        with get_session() as session:
            paper = upsert_paper(session, test_paper_data)
            session.commit()
            paper_id = paper.id

        # mock 返回不同的值
        mock_result = {
            "doi": "10.5555/example.crossref-update",
            "abstract": "New abstract from CrossRef",
            "year": 2021,
            "journal": {"title": "New Journal from CrossRef"},
            "raw": {"mock": "data"},
            "authors": [],
        }

        def mock_fetch(*args, **kwargs):
            return mock_result

        import core.crossref_api
        monkeypatch.setattr(core.crossref_api, "fetch_metadata_by_doi", mock_fetch)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "enrich_crossref", "scripts/enrich_crossref.py"
        )
        assert spec is not None and spec.loader is not None
        enrich_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(enrich_module)

        import sys
        original_argv = sys.argv
        sys.argv = ["enrich_crossref.py", "--paper-id", str(paper_id)]
        try:
            enrich_module.main()
        finally:
            sys.argv = original_argv

        # 验证：原有字段保留，只有 doi_meta 被更新
        with get_session() as session:
            stmt = select(Paper).where(Paper.id == paper_id)
            paper = session.scalar(stmt)
            assert paper is not None
            assert paper.doi_meta == mock_result["raw"]  # 更新
            assert paper.abstract == test_paper_data["abstract"]  # 保留原有
            assert paper.year == test_paper_data["year"]  # 保留原有
            assert paper.journal_name == test_paper_data["journal_name"]  # 保留原有

    finally:
        # 恢复全局状态
        paper_library.db._engine = original_engine
        paper_library.db._session_factory = original_session_factory
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("DATABASE_URL", None)
