from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers import authors as authors_router
from paper_library.models import (
    AffiliationLocation,
    AffiliationGeoEvidence,
    Author,
    Base,
    Paper,
    paper_authors,
)


def test_map_label_and_coordinates_use_same_trusted_location(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        location = AffiliationLocation(
            id=10,
            affiliation_zh="天津市环湖医院",
            city="天津",
            province="天津",
            country="China",
            lat=39.1,
            lng=117.2,
        )
        session.add(location)
        session.flush()
        paper = Paper(title="Map consistency test")
        author = Author(
            name="Example Author", city="南京市", province="江苏省",
            country="China", canonical_affiliation_id=location.id,
        )
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id, author_id=author.id, author_order=0,
            is_first=True, is_corresponding=True,
        ))
        session.add(AffiliationGeoEvidence(
            affiliation_location_id=10,
            provider="amap", query_text="天津市环湖医院",
            audit_status="confirmed",
        ))
        session.commit()

    @contextmanager
    def test_session(*_args, **_kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(authors_router, "get_session", test_session)
    response = authors_router.get_author_geo_data(
        country=None, province=None, city=None, scope="all", current_user={},
    )

    assert response["total_locations"] == 1
    location = response["locations"][0]
    assert location["affiliation"] == "天津市环湖医院"
    assert location["city"] == "天津"
    assert location["province"] == "天津"
    assert location["lat"] == 39.1
    assert location["lng"] == 117.2


def test_map_excludes_unresolved_geography(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        location = AffiliationLocation(
            id=11,
            affiliation_zh="待审核机构",
            city="杭州市",
            province="浙江省",
            country="China",
            lat=30.2,
            lng=120.2,
        )
        session.add(location)
        session.flush()
        paper = Paper(title="Unresolved location test")
        author = Author(
            name="Example Author", canonical_affiliation_id=location.id,
        )
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id, author_id=author.id, author_order=0,
            is_first=True, is_corresponding=True,
        ))
        session.add(AffiliationGeoEvidence(
            affiliation_location_id=11,
            provider="amap", query_text="待审核机构",
            audit_status="needs_review",
        ))
        session.commit()

    @contextmanager
    def test_session(*_args, **_kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(authors_router, "get_session", test_session)
    response = authors_router.get_author_geo_data(
        country=None, province=None, city=None, scope="all", current_user={},
    )
    assert response == {"locations": [], "total_locations": 0}


def test_author_profile_respects_paper_review_visibility(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        author = Author(name="Lifecycle visibility author")
        papers = [
            Paper(title="Approved paper", review_outcome="approved"),
            Paper(title="Unreviewed paper", review_outcome="unreviewed"),
            Paper(title="Excluded paper", review_outcome="excluded"),
        ]
        session.add_all([author, *papers])
        session.flush()
        author_id = author.id
        for order, paper in enumerate(papers):
            session.execute(paper_authors.insert().values(
                paper_id=paper.id, author_id=author_id, author_order=order,
                is_first=order == 0, is_corresponding=order == 0,
            ))
        session.commit()

    @contextmanager
    def test_session(*_args, **_kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(authors_router, "get_session", test_session)
    try:
        visible_by_role = {}
        for role in ("limited", "viewer", "reviewer"):
            response = authors_router.get_author_profile(
                author_id=author_id, current_user={"role": role},
            )
            visible_by_role[role] = {paper["title"] for paper in response["papers"]}

        assert visible_by_role["limited"] == {"Approved paper"}
        assert visible_by_role["viewer"] == {"Approved paper", "Unreviewed paper"}
        assert visible_by_role["reviewer"] == {
            "Approved paper", "Unreviewed paper", "Excluded paper",
        }
    finally:
        engine.dispose()
