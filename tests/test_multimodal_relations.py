from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routers.papers import _build_base_query
from paper_library.models import Base, Paper


def test_multimodal_timing_filter_is_bound_to_selected_method(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'papers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add_all([
            Paper(
                title="EEG concurrent, TMS pre-post",
                uses_multimodal=True,
                multimodal_methods=["TMS", "EEG"],
                multimodal_relations=[
                    {"method": "EEG", "timing": "concurrent"},
                    {"method": "TMS", "timing": "pre_post"},
                ],
            ),
            Paper(
                title="TMS concurrent",
                uses_multimodal=True,
                multimodal_methods=["TMS"],
                multimodal_relations=[{"method": "TMS", "timing": "concurrent"}],
            ),
        ])
        session.commit()

        results = _build_base_query(
            session,
            q=None,
            journal=None,
            year_from=None,
            year_to=None,
            multimodal_technique="TMS",
            multimodal_timing="concurrent",
        ).all()

        assert [paper.title for paper in results] == ["TMS concurrent"]

    engine.dispose()


def test_multimodal_timing_filter_without_method_matches_any_relation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'papers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    with factory() as session:
        session.add_all([
            Paper(
                title="Known timing",
                multimodal_relations=[{"method": "fMRI", "timing": "concurrent"}],
            ),
            Paper(title="Unknown timing", multimodal_relations=[]),
        ])
        session.commit()

        results = _build_base_query(
            session,
            q=None,
            journal=None,
            year_from=None,
            year_to=None,
            multimodal_timing="concurrent",
        ).all()

        assert [paper.title for paper in results] == ["Known timing"]

    engine.dispose()
