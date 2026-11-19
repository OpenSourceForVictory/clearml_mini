"""Immutable per-attempt shared output location; no file content storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'a730d12'
down_revision = 'z629c01'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('shared_artifact_roots',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('execution_id', sa.String(36), nullable=False),
        sa.Column('snapshot', sa.JSON().with_variant(JSONB, 'postgresql'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('shared_artifact_roots')
