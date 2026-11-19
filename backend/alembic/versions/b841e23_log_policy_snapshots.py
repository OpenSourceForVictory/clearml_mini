"""Freeze the acknowledged log budget for each execution attempt."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='b841e23'
down_revision='a730d12'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('execution_log_policies',
        sa.Column('run_id',sa.String(36),sa.ForeignKey('evaluation_runs.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('snapshot',sa.JSON().with_variant(JSONB,'postgresql'),nullable=False))


def downgrade():
    op.drop_table('execution_log_policies')
