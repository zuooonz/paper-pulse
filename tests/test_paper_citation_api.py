from api.routers import papers as papers_router
from api.routers.papers import detail as papers_detail
from paper_library.models import Paper


def test_live_apa_fallback_uses_all_ordered_paper_authors(monkeypatch):
    paper = Paper(
        id=3143,
        internal_id=676,
        title="Synthetic qMRI study",
        journal_name="Example Journal of Imaging Tests",
        year=2026,
        doi="10.5555/example.paper-citation",
        doi_meta=None,
    )
    monkeypatch.setattr(
        papers_detail,
        "_get_ordered_authors",
        lambda session, paper_id: [
            {"name": "Xuefeng Shan", "author_order": 0},
            {"name": "Aster Example", "author_order": 1},
            {"name": "Beryl Sample", "author_order": 2},
        ],
    )

    citation = papers_router._build_apa_citation(paper, session=object())

    assert citation.startswith("Shan, X., Example, A., & Sample, B. (2026).")
