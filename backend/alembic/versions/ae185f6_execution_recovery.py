"""Retain recovery identity and late receipt evidence independently of runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'ae185f6'
down_revision = 'ad074e5'
branch_labels = None
depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.create_table('execution_recovery',
        sa.Column('execution_id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), nullable=False, unique=True),
        sa.Column('worker_id', sa.String(36), nullable=False),
        sa.Column('slot_id', sa.String(36), nullable=False),
        sa.Column('generation', sa.String(36), nullable=False),
        sa.Column('policy', JSON, nullable=False),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('received_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('received_records', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('execution_recovery_receipts',
        sa.Column('execution_id', sa.String(36), sa.ForeignKey('execution_recovery.execution_id'), primary_key=True),
        sa.Column('kind', sa.String(20), primary_key=True),
        sa.Column('sequence', sa.BigInteger(), primary_key=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('payload', JSON, nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    # An old scheduler cannot interpret these grants. Never silently destroy
    # authority or evidence for an execution that may still exist on a node.
    if op.get_bind().execute(sa.text('SELECT 1 FROM execution_recovery LIMIT 1')).first():
        raise RuntimeError('recovery records remain; reconcile and archive before downgrade')
    op.drop_table('execution_recovery_receipts')
    op.drop_table('execution_recovery')
