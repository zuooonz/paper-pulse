"""Author merge audit log — kept for traceability.

The affiliation_canonical and affiliation_mapping tables have been
merged into affiliation_locations + authors.canonical_affiliation_id.
"""

import datetime
from sqlalchemy import Column, Integer, String, DateTime
from paper_library.models import Base


class AuthorMergeLog(Base):
    """Audit log for author deduplication merges."""
    __tablename__ = "author_merge_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kept_author_id = Column(Integer, nullable=False, index=True)
    merged_author_id = Column(Integer, nullable=False, index=True)
    reason = Column(String(100), nullable=False)
    confidence = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
