from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.routers.papers import _build_base_query
from paper_library.models import Author, Base, Paper, paper_authors


def test_configured_topic_filter_does_not_depend_on_author_institution(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'papers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add_all([
            Paper(title="Study one", research_topics=["肿瘤"], neuroscience_subtopics=["补充值"]),
            Paper(title="Study two", research_topics=["肿瘤"], neuroscience_subtopics=["补充值"]),
            Paper(title="Study three", research_topics=["心血管"]),
        ])
        session.commit()
        results = _build_base_query(
            session,
            q=None,
            journal=None,
            year_from=None,
            year_to=None,
            research_topic="肿瘤",
            neuroscience_subtopic="补充值",
        ).all()
        assert [paper.title for paper in results] == ["Study one", "Study two"]
    engine.dispose()


def test_department_filter_uses_the_authors_own_institution_type(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'papers.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        paper = Paper(title="Department record")
        author = Author(name="Study Author", department="神经内科", institution_type="临床")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=author.id,
            author_order=0,
            is_corresponding=True,
        ))
        session.commit()
        results = _build_base_query(
            session,
            q=None,
            journal=None,
            year_from=None,
            year_to=None,
            department="神经内科",
        ).all()
        assert [item.title for item in results] == ["Department record"]
    engine.dispose()
