"""Independent bounded execution log state; legacy logs remain unchanged."""
from alembic import op
import sqlalchemy as sa
revision = 'y518b90'
down_revision = 'x407a89'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('run_log_states',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id'), primary_key=True),
        sa.Column('next_sequence', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('first_sequence', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('retained_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_digest', sa.String(64)),
        sa.Column('truncated', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('run_log_chunks',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id'), primary_key=True),
        sa.Column('sequence', sa.BigInteger(), primary_key=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('stream', sa.String(10), nullable=False),
        sa.Column('gap', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False))


def downgrade():
    op.drop_table('run_log_chunks')
    op.drop_table('run_log_states')
