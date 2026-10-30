from dataclasses import dataclass

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from paper_library.author_reconciliation import (
    SourceAuthor,
    purge_reconciliation_orphans,
    reconcile_combined_author_paper,
)
from paper_library.author_identity import add_author_identifier
from paper_library.models import (
    Author,
    Base,
    Paper,
    PaperAuthorMention,
    paper_authors,
)


@dataclass
class Fact:
    role: str
    name: str
    affiliation: str
    source_ref: str = "workbook:row"


def test_reconcile_splits_combined_author_and_keeps_languages_separate():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=736, doi="10.5555/example.reconciliation-736")
        other_paper = Paper(title="Other")
        yang = Author(name="Beta Example", name_zh="Beta Example")
        combined = Author(name="Alpha Sample 、 Beta Example", name_zh="Alpha Sample")
        session.add_all([paper, other_paper, yang, combined])
        session.flush()
        session.execute(paper_authors.insert(), [
            {"paper_id": paper.id, "author_id": yang.id, "author_order": 0,
             "is_first": False, "is_corresponding": True},
            {"paper_id": paper.id, "author_id": combined.id, "author_order": 1,
             "is_first": True, "is_corresponding": False},
            {"paper_id": other_paper.id, "author_id": yang.id, "author_order": 0,
             "is_first": True, "is_corresponding": False},
        ])
        result = reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[
                SourceAuthor(0, "Alpha Sample", "Example University School of Physical Education"),
                SourceAuthor(1, "Beta Example", "Example University School of Physical Education", "contact1@example.org"),
            ],
            excel_facts=[
                Fact("first", "Alpha Sample", "示例大学体育学院"),
                Fact("corresponding", "Beta Example", "示例大学体育学院"),
            ],
        )
        session.flush()

        rows = session.execute(
            select(paper_authors, Author.name)
            .join(Author, Author.id == paper_authors.c.author_id)
            .where(paper_authors.c.paper_id == paper.id)
            .order_by(paper_authors.c.author_order)
        ).all()
        assert [row.name for row in rows] == ["Alpha Sample", "Beta Example"]
        assert rows[0].is_first is True
        assert rows[1].is_corresponding is True
        mentions = session.scalars(
            select(PaperAuthorMention)
            .where(PaperAuthorMention.paper_id == paper.id)
            .order_by(PaperAuthorMention.name_raw)
        ).all()
        assert {row.name_raw for row in mentions} == {"Alpha Sample", "Beta Example"}
        assert all(row.name_zh is None for row in mentions)
        assert all(row.affiliation_zh == "示例大学体育学院" for row in mentions)
        # A shared global person is not overwritten by one paper's repair.
        assert session.get(Author, yang.id).name_zh == "Beta Example"
        assert result["relations_removed"] == 1
        cleanup = purge_reconciliation_orphans(session)
        assert cleanup["authors_deleted"] == 1
        assert session.get(Author, combined.id) is None


def test_reconcile_maps_unique_chinese_relation_to_publication_author():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=737, doi="10.5555/example.reconciliation-737")
        combined = Author(name="Alpha Example 、 Xuefeng Shan")
        xiaolei = Author(name="单雪峰", name_zh="单雪峰")
        session.add_all([paper, combined, xiaolei])
        session.flush()
        session.execute(paper_authors.insert(), [
            {"paper_id": paper.id, "author_id": combined.id, "author_order": 0,
             "is_first": True, "is_corresponding": False},
            {"paper_id": paper.id, "author_id": xiaolei.id, "author_order": 1,
             "is_first": False, "is_corresponding": True},
        ])
        reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[
                SourceAuthor(0, "Alpha Example", "Example Unit A"),
                SourceAuthor(1, "Xuefeng Shan", "Example Unit B"),
            ],
            excel_facts=[
                Fact("first", "Alpha Example", "山东示例医院高压氧科"),
                Fact("corresponding", "单雪峰", "山东示例大学心理学院"),
            ],
        )
        session.flush()
        assert session.get(Author, xiaolei.id).name == "Xuefeng Shan"
        assert session.get(Author, xiaolei.id).name_zh == "单雪峰"
        mention = session.scalar(select(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == paper.id,
            PaperAuthorMention.author_id == xiaolei.id,
        ))
        assert mention.name_raw == "Xuefeng Shan"
        assert mention.name_zh == "单雪峰"


def test_reconcile_reuses_existing_orcid_identity():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=800, doi="10.5555/example.reconciliation-800")
        combined = Author(name="First Person 、 Known Person")
        duplicate = Author(name="Known Person")
        known = Author(name="Known Person", orcid="0000-0002-1825-0097")
        session.add_all([paper, combined, duplicate, known])
        session.flush()
        add_author_identifier(
            session,
            author_id=known.id,
            identifier_type="orcid",
            identifier_value="0000-0002-1825-0097",
            source_type="crossref",
        )
        session.execute(paper_authors.insert(), [
            {"paper_id": paper.id, "author_id": combined.id, "author_order": 0,
             "is_first": True, "is_corresponding": False},
            {"paper_id": paper.id, "author_id": duplicate.id, "author_order": 1,
             "is_first": False, "is_corresponding": False},
        ])
        reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[
                SourceAuthor(0, "First Person"),
                SourceAuthor(1, "Known Person", orcid="0000-0002-1825-0097"),
            ],
            excel_facts=[Fact("first", "First Person", "")],
        )
        session.flush()
        linked_ids = set(session.scalars(
            select(paper_authors.c.author_id).where(
                paper_authors.c.paper_id == paper.id
            )
        ))
        assert known.id in linked_ids
        assert duplicate.id not in linked_ids


def test_explicit_source_correspondence_overrides_excel_role():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=731, doi="10.example/731")
        first = Author(name="Alpha Example")
        actual = Author(name="Beta Sample")
        last = Author(name="Gamma Example")
        session.add_all([paper, first, actual, last])
        session.flush()
        session.execute(paper_authors.insert(), [
            {"paper_id": paper.id, "author_id": first.id, "author_order": 0,
             "is_first": True, "is_corresponding": False},
            {"paper_id": paper.id, "author_id": actual.id, "author_order": 1,
             "is_first": False, "is_corresponding": False},
            {"paper_id": paper.id, "author_id": last.id, "author_order": 2,
             "is_first": False, "is_corresponding": True},
        ])

        reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[
                SourceAuthor(0, "Alpha Example"),
                SourceAuthor(1, "Beta Sample", email="contact2@example.org",
                             is_corresponding=True),
                SourceAuthor(2, "Gamma Example"),
            ],
            excel_facts=[
                Fact("first", "单雪峰", "常州示例医院康复医学科"),
                Fact("corresponding", "Gamma Example", "常州示例医院康复医学科"),
            ],
            allow_clean_refresh=True,
        )
        session.flush()

        rows = session.execute(
            select(paper_authors.c.author_order, paper_authors.c.is_corresponding)
            .where(paper_authors.c.paper_id == paper.id)
            .order_by(paper_authors.c.author_order)
        ).all()
        assert rows == [(0, False), (1, True), (2, False)]


def test_corresponding_affiliation_wins_over_same_author_first_role():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=703, doi="10.example/703")
        author = Author(name="Xuefeng Shan")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert(), [{
            "paper_id": paper.id, "author_id": author.id, "author_order": 0,
            "is_first": True, "is_corresponding": True,
        }])

        reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[SourceAuthor(
                0, "Xuefeng Shan", is_corresponding=True,
            )],
            excel_facts=[
                Fact("corresponding", "单雪峰", "示例医院医学心理科"),
                Fact("first", "单雪峰", "示例大学心理学部"),
            ],
            allow_clean_refresh=True,
        )
        session.flush()

        mention = session.scalar(select(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == paper.id,
            PaperAuthorMention.author_id == author.id,
        ))
        assert mention.affiliation_zh == "示例医院医学心理科"


def test_conflicting_same_role_affiliations_are_not_guessed():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=703, doi="10.example/703")
        author = Author(name="Xuefeng Shan")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert(), [{
            "paper_id": paper.id, "author_id": author.id, "author_order": 0,
            "is_first": False, "is_corresponding": True,
        }])

        reconcile_combined_author_paper(
            session,
            paper=paper,
            source_authors=[SourceAuthor(
                0, "Xuefeng Shan", is_corresponding=True,
            )],
            excel_facts=[
                Fact("corresponding", "单雪峰", "示例大学教育学院"),
                Fact("corresponding", "单雪峰", "示例医院医学心理科"),
            ],
            allow_clean_refresh=True,
        )
        session.flush()

        mention = session.scalar(select(PaperAuthorMention).where(
            PaperAuthorMention.paper_id == paper.id,
            PaperAuthorMention.author_id == author.id,
        ))
        assert mention.affiliation_zh is None
