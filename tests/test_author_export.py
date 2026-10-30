import csv
import io

from sqlalchemy import String, select, text

from api.routers import authors as authors_router
from api.routers import papers as papers_router
from paper_library.db import get_session_factory
from paper_library.models import (
    AffiliationGeoEvidence,
    AffiliationLocation,
    Author,
    Paper,
    paper_authors,
)


def _export_rows(monkeypatch, **filters):
    options = {"country": None, "province": None, "city": None, "scope": "all"}
    options.update(filters)

    class CapturedStreamingResponse:
        def __init__(self, content, **_kwargs):
            self.content = content.read()

    monkeypatch.setattr("fastapi.responses.StreamingResponse", CapturedStreamingResponse)
    response = authors_router.export_authors(current_user={}, **options)
    content = response.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))


def test_export_author_scopes_and_country_filters(monkeypatch):
    monkeypatch.setattr(authors_router, "get_target_label", lambda: "TargetCo")
    session_factory = get_session_factory()

    with session_factory() as session:
        china = AffiliationLocation(
            affiliation_zh="中国机构",
            city="北京",
            province="北京",
            country="China",
        )
        united_states = AffiliationLocation(
            affiliation_zh="美国机构",
            city="Boston",
            province="Massachusetts",
            country="United States",
        )
        session.add_all([china, united_states])
        session.flush()

        alice = Author(name="Alice Example", name_zh="示例甲", canonical_affiliation_id=china.id)
        bob = Author(name="Bob Example", name_zh="示例乙", canonical_affiliation_id=china.id)
        carol = Author(name="Carol Example", canonical_affiliation_id=united_states.id)
        session.add_all([alice, bob, carol])
        session.flush()

        target_china = Paper(
            title="Target paper",
            doi="10.5555/target-paper",
            pmid="10001",
            entity_label="TargetCo",
        )
        other_china = Paper(
            title="Other paper",
            doi="10.5555/other-paper",
            pmid="10002",
            entity_label="OtherCo",
        )
        other_china_2 = Paper(
            title="Another other paper",
            doi="10.5555/other-paper-2",
            pmid="10003",
            entity_label="OtherCo",
        )
        target_us = Paper(
            title="Target paper in another country",
            doi="10.5555/target-us-paper",
            pmid="10004",
            entity_label="TargetCo",
        )
        session.add_all([target_china, other_china, other_china_2, target_us])
        session.flush()

        session.execute(paper_authors.insert(), [
            {
                "paper_id": target_china.id,
                "author_id": alice.id,
                "author_order": 0,
                "is_corresponding": True,
                "is_first": False,
            },
            {
                "paper_id": other_china.id,
                "author_id": alice.id,
                "author_order": 0,
                "is_corresponding": False,
                "is_first": True,
            },
            {
                "paper_id": other_china.id,
                "author_id": bob.id,
                "author_order": 1,
                "is_corresponding": False,
                "is_first": True,
            },
            {
                "paper_id": other_china_2.id,
                "author_id": bob.id,
                "author_order": 0,
                "is_corresponding": False,
                "is_first": False,
            },
            {
                "paper_id": target_us.id,
                "author_id": carol.id,
                "author_order": 0,
                "is_corresponding": True,
                "is_first": False,
            },
        ])
        session.add_all([
            AffiliationGeoEvidence(
                affiliation_location_id=china.id,
                provider="test",
                query_text="中国机构",
                audit_status="confirmed",
            ),
            AffiliationGeoEvidence(
                affiliation_location_id=united_states.id,
                provider="test",
                query_text="美国机构",
                audit_status="confirmed",
            ),
        ])
        session.commit()

    target_rows = _export_rows(monkeypatch, scope="target")
    assert {row["姓名"] for row in target_rows} == {"Alice Example", "Carol Example"}

    all_rows = _export_rows(monkeypatch, scope="all")
    assert {row["姓名"] for row in all_rows} == {
        "Alice Example", "Bob Example", "Carol Example",
    }
    all_by_name = {row["姓名"]: row for row in all_rows}
    assert all_by_name["Alice Example"]["角色"] == "通讯作者"
    assert all_by_name["Alice Example"]["论文数"] == "2"
    assert all_by_name["Alice Example"]["目标课题论文数"] == "1"
    assert set(all_by_name["Alice Example"]["DOI"].split(",")) == {
        "10.5555/target-paper", "10.5555/other-paper",
    }
    assert set(all_by_name["Alice Example"]["PMID"].split(",")) == {"10001", "10002"}

    china_rows = _export_rows(monkeypatch, scope="all", country="中国")
    assert {row["姓名"] for row in china_rows} == {"Alice Example", "Bob Example"}

    target_china_rows = _export_rows(monkeypatch, scope="target", country="中国")
    assert {row["姓名"] for row in target_china_rows} == {"Alice Example"}


def test_postgres_safe_author_role_and_json_filters():
    session_factory = get_session_factory()
    with session_factory() as session:
        paper = Paper(
            title="JSON brain region filter",
            entity_label="TargetCo",
            brain_regions=["frontal cortex"],
        )
        author = Author(name="Boolean filter author")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=author.id,
            author_order=0,
            is_corresponding=True,
            is_first=False,
        ))
        session.flush()

        assert session.scalar(select(Paper.id).where(
            Paper.id == paper.id,
            Paper.brain_regions.isnot(None),
            Paper.brain_regions.cast(String).ilike("%frontal cortex%"),
        )) == paper.id

        rows = session.execute(text("""
            SELECT pa.paper_id
            FROM paper_authors pa
            JOIN papers p ON p.id = pa.paper_id
            WHERE pa.is_corresponding IS TRUE AND p.entity_label = :target_label
        """), {"target_label": "TargetCo"}).scalars().all()
        assert rows == [paper.id]


def test_paper_author_role_updates_use_boolean_expressions():
    session_factory = get_session_factory()
    with session_factory() as session:
        paper = Paper(title="Role update paper")
        author = Author(name="Role update author")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=author.id,
            author_order=3,
            is_corresponding=False,
            is_first=False,
        ))
        author_data = {"name": author.name}

        papers_router._upsert_paper_author_link(
            session, paper.id, author, 0, True, False, author_data,
        )
        papers_router._upsert_paper_author_link(
            session, paper.id, author, 1, False, True, author_data,
        )
        row = session.execute(select(paper_authors).where(
            paper_authors.c.paper_id == paper.id,
            paper_authors.c.author_id == author.id,
        )).one()._mapping
        assert row["author_order"] == 3
        assert row["is_first"] is True
        assert row["is_corresponding"] is True

        papers_router._upsert_paper_author_link(
            session, paper.id, author, 8, False, True, author_data, update_order=True,
        )
        row = session.execute(select(paper_authors).where(
            paper_authors.c.paper_id == paper.id,
            paper_authors.c.author_id == author.id,
        )).one()._mapping
        assert row["author_order"] == 8
        assert row["is_first"] is False
        assert row["is_corresponding"] is True
