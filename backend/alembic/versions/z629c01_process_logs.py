"""Process log catalog and bounded, temporary read requests; preserve v2 logs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='z629c01'
down_revision='y518b90'
branch_labels=None
depends_on=None
J=sa.JSON().with_variant(JSONB,'postgresql')


def upgrade():
    op.create_table('execution_log_catalog',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('run_id',sa.String(36),sa.ForeignKey('evaluation_runs.id'),nullable=False),
        sa.Column('execution_id',sa.String(36),nullable=False),
        sa.Column('worker_id',sa.String(36),sa.ForeignKey('workers.id'),nullable=False),
        sa.Column('slot_id',sa.String(36),sa.ForeignKey('gpu_slots.id'),nullable=False),
        sa.Column('scope',sa.String(16),nullable=False),sa.Column('episode_id',sa.Integer(),nullable=False),
        sa.Column('generation',sa.String(36),nullable=False),sa.Column('root_path',sa.String(4096),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('first_sequence',sa.BigInteger(),nullable=False),sa.Column('next_sequence',sa.BigInteger(),nullable=False),
        sa.Column('dropped_bytes',sa.BigInteger(),nullable=False),sa.Column('truncated',sa.Boolean(),nullable=False),
        sa.Column('policy_revision',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('execution_id','scope','episode_id'))
    for key in ['run_id','worker_id']:op.create_index('ix_execution_log_catalog_'+key,'execution_log_catalog',[key])
    op.create_table('execution_log_reads',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('log_id',sa.String(36),sa.ForeignKey('execution_log_catalog.id'),nullable=False),
        sa.Column('run_id',sa.String(36),sa.ForeignKey('evaluation_runs.id'),nullable=False),
        sa.Column('worker_id',sa.String(36),sa.ForeignKey('workers.id'),nullable=False),
        sa.Column('budget_slot',sa.Integer(),nullable=False,unique=True),
        sa.Column('fingerprint',sa.String(64),nullable=False),sa.Column('generation',sa.String(36),nullable=False),
        sa.Column('query',J,nullable=False),sa.Column('status',sa.String(20),nullable=False),
        sa.Column('claim_hash',sa.String(64)),sa.Column('response',J),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    for key in ['log_id','run_id','worker_id','fingerprint','expires_at']:op.create_index('ix_execution_log_reads_'+key,'execution_log_reads',[key])
    op.create_table('execution_log_read_subscribers',
        sa.Column('request_id',sa.String(36),sa.ForeignKey('execution_log_reads.id'),primary_key=True),
        sa.Column('user_id',sa.String(36),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('access_id',sa.String(36),nullable=False,unique=True))


def downgrade():
    op.drop_table('execution_log_read_subscribers')
    op.drop_table('execution_log_reads')
    op.drop_table('execution_log_catalog')
