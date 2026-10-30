"""Replace the combined ingestion state with independent lifecycle fields.

The downgrade mapping is intentionally approximate: the previous state could
not preserve processing, review, PDF, and metadata completeness independently.
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_paper_lifecycle"
down_revision = "0014_rename_entity_columns"
branch_labels = None
depends_on = None


def _map_legacy_lifecycle(
    old_status: str | None,
    processing_error: str | None,
) -> tuple[str, str, str | None]:
    """Map one legacy status and error to the independent lifecycle fields."""
    if old_status in {"pending", "pending_enrichment", None}:
        return "pending", "unreviewed", processing_error
    if old_status in {"processing", "downloading"}:
        return "processing", "unreviewed", processing_error
    if old_status in {"pending_review", "pdf_missing"}:
        return "done", "unreviewed", processing_error
    if old_status == "ready":
        return "done", "approved", processing_error
    if old_status == "error":
        return "failed", "unreviewed", processing_error
    unknown_note = f"unknown ingestion_status: {old_status}"
    error = f"{unknown_note}; {processing_error}" if processing_error else unknown_note
    return "failed", "unreviewed", error


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("papers", sa.Column("processing_status", sa.String(20), nullable=False, server_default="pending"))
    op.add_column("papers", sa.Column("processing_error", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("review_outcome", sa.String(20), nullable=False, server_default="unreviewed"))
    op.add_column("papers", sa.Column("reviewed_by", sa.String(100), nullable=True))
    op.add_column("papers", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("papers", sa.Column("review_note", sa.Text(), nullable=True))
    op.add_column("papers", sa.Column("has_pdf", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("papers", sa.Column("metadata_complete", sa.Boolean(), nullable=False, server_default=sa.false()))

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("papers")}
    if "ingestion_status" not in columns:
        # The migration is defined against the current schema; fail clearly if
        # the expected source field is absent rather than silently losing data.
        raise RuntimeError("papers.ingestion_status is required for lifecycle migration")

    rows = bind.execute(sa.text(
        "SELECT id, ingestion_status, ingestion_error, pdf_storage_path, title, doi, pmid, year, journal_name, abstract "
        "FROM papers"
    )).mappings().all()
    author_table = "paper_authors" if "paper_authors" in tables else None
    for row in rows:
        old = row["ingestion_status"]
        old_value = str(old) if old is not None else None
        processing, review, error = _map_legacy_lifecycle(
            old_value, row["ingestion_error"],
        )

        author_exists = False
        if author_table:
            author_exists = bool(bind.execute(
                sa.text("SELECT 1 FROM paper_authors WHERE paper_id = :paper_id LIMIT 1"),
                {"paper_id": row["id"]},
            ).first())
        complete = bool(
            str(row["title"] or "").strip()
            and (str(row["doi"] or "").strip() or str(row["pmid"] or "").strip())
            and row["year"] is not None
            and str(row["journal_name"] or "").strip()
            and str(row["abstract"] or "").strip()
            and author_exists
        )
        bind.execute(sa.text(
            "UPDATE papers SET processing_status=:processing, processing_error=:error, "
            "review_outcome=:review, has_pdf=:has_pdf, metadata_complete=:complete WHERE id=:id"
        ), {
            "processing": processing,
            "error": error,
            "review": review,
            "has_pdf": row["pdf_storage_path"] is not None,
            "complete": complete,
            "id": row["id"],
        })

    op.create_index("ix_papers_processing_status", "papers", ["processing_status"], unique=False)
    op.create_index("ix_papers_review_outcome", "papers", ["review_outcome"], unique=False)
    op.create_index("ix_papers_has_pdf", "papers", ["has_pdf"], unique=False)
    op.create_index("ix_papers_metadata_complete", "papers", ["metadata_complete"], unique=False)
    with op.batch_alter_table("papers") as batch_op:
        batch_op.drop_index("ix_papers_ingestion_status")
        batch_op.drop_column("ingestion_status")
        batch_op.drop_column("ingestion_error")


def downgrade() -> None:
    """Restore a lossy approximation of the previous combined state."""
    bind = op.get_bind()
    op.add_column("papers", sa.Column("ingestion_status", sa.String(50), nullable=False, server_default="pending"))
    op.add_column("papers", sa.Column("ingestion_error", sa.Text(), nullable=True))
    rows = bind.execute(sa.text(
        "SELECT id, processing_status, processing_error, review_outcome, has_pdf FROM papers"
    )).mappings().all()
    for row in rows:
        if row["processing_status"] == "failed":
            status = "error"
        elif row["processing_status"] == "processing":
            status = "processing"
        elif row["processing_status"] == "done":
            status = "ready" if row["review_outcome"] == "approved" else "pending_review"
        else:
            status = "pending"
        if not row["has_pdf"] and status == "pending":
            status = "pdf_missing"
        bind.execute(sa.text(
            "UPDATE papers SET ingestion_status=:status, ingestion_error=:error WHERE id=:id"
        ), {"status": status, "error": row["processing_error"], "id": row["id"]})

    for name in (
        "ix_papers_metadata_complete", "ix_papers_has_pdf",
        "ix_papers_review_outcome", "ix_papers_processing_status",
    ):
        op.drop_index(name, table_name="papers")
    with op.batch_alter_table("papers") as batch_op:
        batch_op.create_index("ix_papers_ingestion_status", ["ingestion_status"], unique=False)
        batch_op.drop_column("metadata_complete")
        batch_op.drop_column("has_pdf")
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_outcome")
        batch_op.drop_column("processing_error")
        batch_op.drop_column("processing_status")
