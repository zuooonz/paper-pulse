"""数据库抽象层测试"""
import tempfile
import os
import pytest
from sqlalchemy import inspect, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, QueuePool

import paper_library.db as db_module
from paper_library.db import (
    get_engine,
    get_session_factory,
    init_db,
    reset_engines,
)
from paper_library.models import AccountBase, Base, DraftBase, Paper
from paper_library.governance_models import GovernanceBase
from paper_library.crud import (
    upsert_paper,
    get_paper_by_id,
    get_paper_by_doi,
    get_papers_ready,
)
from paper_library.paper_lifecycle import fail_processing


def test_init_db(tmp_path, monkeypatch):
    """初始化一份包含论文、账号、草稿和治理表的单库。"""
    database_path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    monkeypatch.setenv("DB_INIT_MODE", "create_all")
    reset_engines()

    init_db(str(tmp_path))
    engine = get_engine(str(tmp_path))
    tables = set(inspect(engine).get_table_names())

    assert {"papers", "journals", "article_drafts", "draft_papers"} <= tables
    assert {"channel_accounts", "dingtalk_users"} <= tables
    assert {"quota_policies", "audit_events", "summary_jobs", "jobs", "scheduler_leases"} <= tables
    assert Base is AccountBase is DraftBase is GovernanceBase
    assert get_session_factory(str(tmp_path)).kw["bind"] is engine
    assert isinstance(engine.pool, NullPool)

    with engine.connect() as connection:
        draft_fks = inspect(connection).get_foreign_keys("draft_papers")
        assert {tuple(fk["constrained_columns"]) for fk in draft_fks} == {("draft_id",), ("paper_id",)}
        publish_fks = inspect(connection).get_foreign_keys("publish_records")
        account_fk = next(fk for fk in publish_fks if fk["referred_table"] == "channel_accounts")
        assert account_fk["options"]["ondelete"] == "SET NULL"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30000

    reset_engines()


def test_postgresql_engine_factory_uses_configured_queue_pool(monkeypatch, tmp_path):
    captured = {}
    sentinel = object()

    def fake_create_engine(url, **options):
        captured["url"] = url
        captured.update(options)
        return sentinel

    runtime_dir = tmp_path / "config"
    runtime_dir.mkdir()
    (runtime_dir / "runtime.yaml").write_text(
        "database:\n  pool_size: 3\n  max_overflow: 5\n  pool_recycle: 600\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CONFIG_DIR", "config")
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_RECYCLE"):
        monkeypatch.delenv(name, raising=False)
    assert db_module._configured_pool_options() == {
        "pool_size": 3,
        "max_overflow": 5,
        "pool_recycle": 600,
    }
    monkeypatch.setenv("DB_POOL_SIZE", "4")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "7")
    monkeypatch.setenv("DB_POOL_RECYCLE", "900")
    monkeypatch.setattr(db_module, "create_engine", fake_create_engine)

    engine = db_module._create_engine("postgresql://localhost/app")

    assert engine is sentinel
    assert captured["poolclass"] is QueuePool
    assert captured["pool_size"] == 4
    assert captured["max_overflow"] == 7
    assert captured["pool_recycle"] == 900
    assert captured["pool_pre_ping"] is True


def test_postgresql_engine_can_set_statement_timeout(monkeypatch):
    captured = {}
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "45000")
    monkeypatch.setattr(db_module, "create_engine", lambda url, **options: captured.update(options) or object())

    db_module._create_engine("postgresql://localhost/app")

    assert captured["connect_args"] == {"options": "-c statement_timeout=45000"}


def test_postgresql_engine_missing_driver_has_install_hint(monkeypatch):
    def missing_driver(*_args, **_kwargs):
        raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")

    monkeypatch.setattr(db_module, "create_engine", missing_driver)

    with pytest.raises(RuntimeError, match=r"paper-pulse\[postgres\]"):
        db_module._create_engine("postgresql+psycopg://localhost/app")


def test_session_scope_rolls_back_on_process_interrupt():
    class SessionStub:
        rolled_back = False
        closed = False

        def commit(self):
            raise AssertionError("commit should not run after an interrupt")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    session = SessionStub()
    with pytest.raises(KeyboardInterrupt):
        with db_module._session_scope(lambda: session):
            raise KeyboardInterrupt()

    assert session.rolled_back is True
    assert session.closed is True


def test_upsert_paper_new():
    """测试插入新论文，验证字段"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with factory() as session:
            paper_data = {
                "doi": "10.5555/example.qmri-db-record",
                "title": "Synthetic qMRI parameter-mapping benchmark",
                "title_zh": "合成qMRI参数映射基准研究",
                "year": 2023,
                "journal_name": "Example Journal of Quantitative Imaging",
                "cites_target": False,
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "not_generated",
            }
            paper = upsert_paper(session, paper_data)
            assert paper.id is not None
            assert paper.doi == paper_data["doi"]
            assert paper.title == paper_data["title"]
            assert paper.cites_target is False
            assert paper.processing_status == "done"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_upsert_paper_dedup_by_doi():
    """测试同一 DOI 插入两次，只有一条记录"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with factory() as session:
            # 第一次插入
            paper_data1 = {
                "doi": "10.5555/example.qmri-db-record",
                "title": "Original Title",
                "processing_status": "pending",
            }
            paper1 = upsert_paper(session, paper_data1)
            id1 = paper1.id

            # 第二次插入相同 DOI
            paper_data2 = {
                "doi": "10.5555/example.qmri-db-record",
                "title": "Updated Title",
                "year": 2023,
                "processing_status": "done",
                "review_outcome": "approved",
            }
            paper2 = upsert_paper(session, paper_data2)

            # 应该是同一条记录
            assert paper2.id == id1
            assert paper2.title == "Updated Title"
            assert paper2.year == 2023

            # 计数应该只有 1 条
            count = session.query(Paper).count()
            assert count == 1

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_get_papers_ready(monkeypatch):
    """测试只返回处理完成且审核通过的未生成论文"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        monkeypatch.setattr("paper_library.crud.generation_requires_target_entity", lambda: False)

        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with factory() as session:
            # 论文 1：done + approved + not_generated → 应该返回
            upsert_paper(session, {
                "doi": "10.1000/test1",
                "title": "Test Paper 1",
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "not_generated",
            })
            # 论文 2：pending → 不返回
            upsert_paper(session, {
                "doi": "10.1000/test2",
                "title": "Test Paper 2",
                "processing_status": "pending",
                "article_status": "not_generated",
            })
            # 论文 3：done + approved + generated → 不返回
            upsert_paper(session, {
                "doi": "10.1000/test3",
                "title": "Test Paper 3",
                "processing_status": "done",
                "review_outcome": "approved",
                "article_status": "generated",
            })
            session.commit()

        with factory() as session:
            ready_papers = get_papers_ready(session, limit=10)
            assert len(ready_papers) == 1
            assert ready_papers[0].doi == "10.1000/test1"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_session_rollback():
    """验证异常时事务回滚"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        # 发生异常应该回滚
        try:
            with factory() as session:
                paper_data = {
                    "doi": "10.1000/test",
                    "title": "Test",
                }
                upsert_paper(session, paper_data)
                raise ValueError("Simulated error")
        except ValueError:
            pass

        # 检查插入被回滚
        with factory() as session:
            count = session.query(Paper).count()
            assert count == 0

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_get_paper_by_id_and_doi():
    """测试按 ID 和 DOI 查询"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with factory() as session:
            paper_data = {
                "doi": "10.1016/j.test.2024.01.001",
                "title": "Test Paper for Query",
            }
            paper = upsert_paper(session, paper_data)
            paper_id = paper.id

            # 按 ID 查询
            found_by_id = get_paper_by_id(session, paper_id)
            assert found_by_id is not None
            assert found_by_id.doi == paper_data["doi"]

            # 按 DOI 查询
            found_by_doi = get_paper_by_doi(session, "10.1016/j.test.2024.01.001")
            assert found_by_doi is not None
            assert found_by_doi.id == paper_id

            # 不存在查询
            not_found = get_paper_by_id(session, 99999)
            assert not_found is None

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass


def test_fail_processing():
    """测试将硬处理失败标记为 failed。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        database_url = f"sqlite:///{db_path}"
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        with factory() as session:
            paper = upsert_paper(session, {
                "doi": "10.1000/status-test",
                "title": "Status Test",
                "processing_status": "pending",
            })
            paper_id = paper.id

            fail_processing(paper, "PDF download failed")
            session.commit()

            updated = get_paper_by_id(session, paper_id)
            assert updated.processing_status == "failed"
            assert updated.processing_error == "PDF download failed"

    finally:
        engine.dispose()
        try:
            os.unlink(db_path)
        except:
            pass
