from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.routers import authors as authors_api
from paper_library.models import (
    AffiliationGeoEvidence,
    AffiliationLocation,
    Author,
    Base,
    Paper,
    paper_authors,
)


def test_authors_geo_search_escapes_wildcards_quotes_and_matches_chinese(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        location = AffiliationLocation(
            affiliation_zh="天津%_研究中心",
            canonical_name_en="Tianjin Research Center",
            city="天津",
            province="天津",
            country="China",
            lat=39.1,
            lng=117.2,
        )
        session.add(location)
        session.flush()
        authors = [
            Author(
                name="Alice 100%_Lab",
                name_zh="爱丽丝",
                affiliation="Unit 100%_Lab",
                affiliation_zh="天津%_研究中心",
                canonical_affiliation_id=location.id,
            ),
            Author(
                name="O'Neil Example",
                name_zh="欧内尔作者",
                affiliation="Research Institute",
                affiliation_zh="研究所",
                canonical_affiliation_id=location.id,
            ),
        ]
        papers = [Paper(title="Author search paper A"), Paper(title="Author search paper B")]
        session.add_all([*authors, *papers])
        session.flush()
        for author, paper in zip(authors, papers):
            session.execute(paper_authors.insert().values(
                paper_id=paper.id,
                author_id=author.id,
                author_order=0,
                is_first=True,
                is_corresponding=True,
            ))
        session.add(AffiliationGeoEvidence(
            affiliation_location_id=location.id,
            provider="amap",
            query_text="天津研究中心",
            audit_status="confirmed",
        ))
        session.commit()

    @contextmanager
    def session_context(*_args, **_kwargs):
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(authors_api, "get_session", session_context)

    def search(term):
        return authors_api.get_author_geo_data(
            country=None, province=None, city=None, search=term,
            scope="all", current_user={},
        )

    assert search("%")['total_locations'] == 1
    assert search("_")['total_locations'] == 1
    assert search("'")['total_locations'] == 1
    assert search("中文")['total_locations'] == 0
    assert search("研究所")['total_locations'] == 1
    assert search("作者")['total_locations'] == 1
    assert search("tIaNjIn ReSeArCh")['total_locations'] == 1
    stats = authors_api.get_author_stats(
        country=None, province=None, city=None, search="欧内尔作者",
        scope="all", current_user={},
    )
    assert stats["total_authors"] == 1
    assert stats["total_papers_mapped"] == 1
    assert "ILIKE" in authors_api._build_author_search_filter("name", "postgresql")[0]
    assert "LIKE" in authors_api._build_author_search_filter("name", "sqlite")[0]
    engine.dispose()
