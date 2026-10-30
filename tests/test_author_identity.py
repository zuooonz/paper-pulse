import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from paper_library.author_identity import (
    add_author_identifier,
    normalize_identifier,
    upsert_paper_author_mention,
)
from paper_library.models import (
    Author,
    AuthorIdentifier,
    Base,
    Paper,
    PaperAuthorMention,
    paper_authors,
)
from api.routers.papers import (
    _get_ordered_authors,
    _sync_full_author_list,
    _upsert_author_from_form,
    _upsert_paper_author_link,
)
from fastapi import HTTPException


def test_normalize_identifiers():
    assert normalize_identifier("email", " contact1@example.org ") == "contact1@example.org"
    assert normalize_identifier("orcid", "https://orcid.org/0000-0002-1825-0097") == "0000-0002-1825-0097"


def test_paper_mention_does_not_overwrite_person():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=437)
        person = Author(
            name="Xuefeng Shan",
            name_zh="单雪峰",
            affiliation_zh="上海示例大学附属医院儿科",
        )
        session.add_all([paper, person])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id,
            author_id=person.id,
            author_order=7,
            is_first=False,
            is_corresponding=True,
        ))
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=person.id,
            name_raw="Xuefeng Shan",
            name_zh="但雪峰",
            affiliation_zh="天津示例大学附属医院康复医学科",
            source_type="manual_verified",
            verification_status="verified",
        )
        session.flush()

        stored_person = session.get(Author, person.id)
        mention = session.scalar(select(PaperAuthorMention))
        assert stored_person.name_zh == "单雪峰"
        assert "上海" in stored_person.affiliation_zh
        assert mention.name_zh == "但雪峰"
        assert "天津" in mention.affiliation_zh


def test_same_email_is_evidence_not_a_global_unique_key():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = Author(name="First Person")
        second = Author(name="Second Person")
        session.add_all([first, second])
        session.flush()
        for person in (first, second):
            add_author_identifier(
                session,
                author_id=person.id,
                identifier_type="email",
                identifier_value="contact3@example.org",
                source_type="paper",
            )
        session.flush()
        rows = session.scalars(select(AuthorIdentifier)).all()
        assert len(rows) == 2
        assert {row.author_id for row in rows} == {first.id, second.id}


def test_orcid_cannot_belong_to_two_people():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = Author(name="First Person")
        second = Author(name="Second Person")
        session.add_all([first, second])
        session.flush()
        add_author_identifier(
            session,
            author_id=first.id,
            identifier_type="orcid",
            identifier_value="0000-0002-1825-0097",
            source_type="paper",
        )
        session.flush()
        with pytest.raises(ValueError, match="another author"):
            add_author_identifier(
                session,
                author_id=second.id,
                identifier_type="orcid",
                identifier_value="0000-0002-1825-0097",
                source_type="paper",
            )


def test_paper_edit_writes_snapshot_and_does_not_reuse_name():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper", internal_id=437)
        shanghai_person = Author(
            name="Xuefeng Shan",
            name_zh="单雪峰",
            affiliation_zh="上海示例大学附属医院儿科",
        )
        session.add_all([paper, shanghai_person])
        session.flush()
        _upsert_paper_author_link(
            session,
            paper.id,
            shanghai_person,
            author_order=7,
            is_first=False,
            is_corresponding=True,
            author_data={
                "name": "Xuefeng Shan",
                "name_zh": "但雪峰",
                "affiliation": "天津示例大学附属医院康复医学科",
            },
        )
        session.flush()
        assert session.get(Author, shanghai_person.id).name_zh == "单雪峰"
        mention = session.scalar(select(PaperAuthorMention))
        assert mention.name_zh == "但雪峰"
        assert "天津" in mention.affiliation_zh

        another = _upsert_author_from_form(session, {"name": "Xuefeng Shan"})
        assert another.id != shanghai_person.id


def test_lower_priority_snapshot_cannot_overwrite_excel_source():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        author = Author(name="Xuefeng Shan")
        session.add_all([paper, author])
        session.flush()
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=author.id,
            name_raw="Xuefeng Shan",
            name_zh="但雪峰",
            affiliation_zh="天津示例大学附属医院康复医学科",
            source_type="excel_source",
            verification_status="source_recorded",
        )
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=author.id,
            name_raw="Xuefeng Shan",
            affiliation_raw="Example University Hospital, Tianjin",
            source_type="crossref_snapshot",
            verification_status="source_recorded",
        )
        mention = session.scalar(select(PaperAuthorMention))
        assert mention.name_zh == "但雪峰"
        assert mention.affiliation_raw.startswith("Example University Hospital")
        assert mention.source_type == "excel_source"


def test_excel_snapshot_preserves_existing_crossref_english_affiliation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        author = Author(name="Xuefeng Shan")
        session.add_all([paper, author])
        session.flush()
        upsert_paper_author_mention(
            session, paper_id=paper.id, author_id=author.id,
            name_raw="Xuefeng Shan", affiliation_raw="English Hospital",
            source_type="crossref_snapshot", verification_status="source_recorded",
        )
        upsert_paper_author_mention(
            session, paper_id=paper.id, author_id=author.id,
            name_raw="Xuefeng Shan", name_zh="但雪峰", affiliation_zh="天津示例医院",
            source_type="excel_source", verification_status="source_recorded",
        )
        mention = session.scalar(select(PaperAuthorMention))
        assert mention.affiliation_raw == "English Hospital"
        assert mention.affiliation_zh == "天津示例医院"
        assert mention.name_zh == "但雪峰"
        assert mention.source_type == "excel_source"


def test_complete_author_list_updates_order_and_removes_stale_link():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        first = Author(name="First Author")
        stale = Author(name="Stale Author")
        last = Author(name="Last Author")
        session.add_all([paper, first, stale, last])
        session.flush()
        for order, author in enumerate((first, stale, last)):
            session.execute(paper_authors.insert().values(
                paper_id=paper.id,
                author_id=author.id,
                author_order=order,
                is_first=order == 0,
                is_corresponding=False,
            ))
        _sync_full_author_list(session, paper.id, [
            {"author_id": last.id, "name": "Last Author", "is_first": True},
            {"author_id": first.id, "name": "First Author", "is_corresponding": True},
        ])
        links = session.execute(
            select(paper_authors).where(paper_authors.c.paper_id == paper.id)
            .order_by(paper_authors.c.author_order)
        ).mappings().all()
        assert [row["author_id"] for row in links] == [last.id, first.id]
        assert links[0]["is_first"] is True
        assert links[1]["is_corresponding"] is True


def test_orcid_identifier_is_used_to_resolve_existing_person():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        author = Author(name="Existing Person")
        session.add(author)
        session.flush()
        add_author_identifier(
            session,
            author_id=author.id,
            identifier_type="orcid",
            identifier_value="0000-0002-1825-0097",
            source_type="crossref",
        )
        resolved = _upsert_author_from_form(session, {
            "name": "Different Spelling",
            "orcid": "https://orcid.org/0000-0002-1825-0097",
        })
        assert resolved.id == author.id


def test_ordered_authors_keep_languages_separate():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        author = Author(name="XUEFENG SHAN", name_zh="单雪峰", affiliation="测试")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id, author_id=author.id, author_order=0,
            is_first=False, is_corresponding=True,
        ))
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=author.id,
            name_raw="XUEFENG SHAN",
            affiliation_raw="Example University, China",
            source_type="crossref_snapshot",
            verification_status="source_recorded",
        )

        displayed = _get_ordered_authors(session, paper.id)[0]
        assert displayed["name_en"] == "XUEFENG SHAN"
        assert displayed["name_zh"] == ""
        assert displayed["affiliation_en"].startswith("Example University")
        assert displayed["affiliation_zh"] == ""


def test_han_relation_name_never_appears_as_english_name():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        author = Author(name="单雪峰", name_zh="单雪峰")
        session.add_all([paper, author])
        session.flush()
        session.execute(paper_authors.insert().values(
            paper_id=paper.id, author_id=author.id, author_order=0,
            is_first=False, is_corresponding=True,
        ))
        upsert_paper_author_mention(
            session,
            paper_id=paper.id,
            author_id=author.id,
            name_raw="单雪峰",
            source_type="relation_snapshot",
            verification_status="unverified",
        )

        displayed = _get_ordered_authors(session, paper.id)[0]
        assert displayed["name_en"] == ""
        assert displayed["name_zh"] == ""


def test_manual_edit_accepts_separate_affiliations():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        paper = Paper(title="Paper")
        author = Author(name="Xuefeng Shan")
        session.add_all([paper, author])
        session.flush()
        _upsert_paper_author_link(
            session, paper.id, author, 0, True, False,
            {
                "name_en": "Xuefeng Shan",
                "name_zh": "但雪峰",
                "affiliation_en": "Example University Hospital, Tianjin",
                "affiliation_zh": "天津示例大学附属医院康复医学科",
            },
        )
        mention = session.scalar(select(PaperAuthorMention))
        assert mention.affiliation_raw.startswith("Example University Hospital")
        assert mention.affiliation_zh == "天津示例大学附属医院康复医学科"


def test_form_rejects_chinese_text_in_english_name():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(HTTPException, match="英文姓名"):
            _upsert_author_from_form(session, {"name_en": "单雪峰"})
