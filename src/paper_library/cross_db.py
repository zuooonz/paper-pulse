"""兼容旧调用方的单库论文-草稿关联辅助函数。"""

from typing import List
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.orm import Session

from paper_library.models import Paper, ArticleDraft, draft_papers


# ── 查询 ──────────────────────────────────────────────────────────

def get_paper_ids_for_draft(draft_id: int, draft_session: Session) -> List[int]:
    """获取某个草稿关联的所有论文 ID。"""
    rows = draft_session.execute(
        select(draft_papers.c.paper_id).where(draft_papers.c.draft_id == draft_id)
    ).fetchall()
    return [r[0] for r in rows]


def get_papers_for_draft(
    draft_id: int, session: Session
) -> List[Paper]:
    """获取某个草稿关联的所有 Paper 对象（按 ID 排序）。"""
    return list(
        session.query(Paper)
        .join(draft_papers, draft_papers.c.paper_id == Paper.id)
        .filter(draft_papers.c.draft_id == draft_id)
        .order_by(Paper.id)
        .all()
    )


def get_draft_ids_for_paper(paper_id: int, draft_session: Session) -> List[int]:
    """获取某篇论文关联的所有草稿 ID。"""
    rows = draft_session.execute(
        select(draft_papers.c.draft_id).where(draft_papers.c.paper_id == paper_id)
    ).fetchall()
    return [r[0] for r in rows]


def get_drafts_for_paper(
    paper_id: int, draft_session: Session
) -> List[ArticleDraft]:
    """获取某篇论文关联的所有 ArticleDraft 对象。"""
    return list(
        draft_session.query(ArticleDraft)
        .join(draft_papers, draft_papers.c.draft_id == ArticleDraft.id)
        .filter(draft_papers.c.paper_id == paper_id)
        .all()
    )


# ── 关联管理 ──────────────────────────────────────────────────────

def link_draft_to_paper(
    draft: ArticleDraft, paper: Paper, draft_session: Session
) -> None:
    """建立草稿与论文的关联。"""
    draft_session.execute(
        draft_papers.insert().values(draft_id=draft.id, paper_id=paper.id)
    )


def unlink_all_papers_from_draft(draft_id: int, draft_session: Session) -> None:
    """移除某个草稿的所有论文关联。"""
    draft_session.execute(
        sa_delete(draft_papers).where(draft_papers.c.draft_id == draft_id)
    )


def unlink_paper_from_all_drafts(paper_id: int, draft_session: Session) -> None:
    """移除某篇论文的所有草稿关联。"""
    draft_session.execute(
        sa_delete(draft_papers).where(draft_papers.c.paper_id == paper_id)
    )


# ── 状态检查 ──────────────────────────────────────────────────────

def draft_has_other_drafts_for_paper(
    draft_id: int, paper_id: int, draft_session: Session
) -> bool:
    """检查某篇论文是否还有其他草稿（排除指定的 draft_id）。"""
    row = draft_session.execute(
        select(draft_papers.c.draft_id)
        .where(
            draft_papers.c.paper_id == paper_id,
            draft_papers.c.draft_id != draft_id,
        )
        .limit(1)
    ).first()
    return row is not None


def paper_has_any_draft(paper_id: int, draft_session: Session) -> bool:
    """检查某篇论文是否有任何草稿。"""
    row = draft_session.execute(
        select(draft_papers.c.draft_id)
        .where(draft_papers.c.paper_id == paper_id)
        .limit(1)
    ).first()
    return row is not None
