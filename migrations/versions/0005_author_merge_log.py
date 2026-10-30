"""Add author merge log table

Revision ID: 0005_author_merge_log
Revises: 0004_affiliation_locations
Create Date: 2026-10-22 02:44:49.077293
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0005_author_merge_log'
down_revision = '0004_affiliation_locations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    
    op.create_table('author_merge_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kept_author_id', sa.Integer(), nullable=False),
    sa.Column('merged_author_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=100), nullable=False),
    sa.Column('confidence', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('author_merge_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_author_merge_log_kept_author_id'), ['kept_author_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_author_merge_log_merged_author_id'), ['merged_author_id'], unique=False)

    


def downgrade() -> None:
    
    with op.batch_alter_table('author_merge_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_author_merge_log_merged_author_id'))
        batch_op.drop_index(batch_op.f('ix_author_merge_log_kept_author_id'))

    op.drop_table('author_merge_log')
    
