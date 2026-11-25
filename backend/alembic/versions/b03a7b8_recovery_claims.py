"""Retain original negotiated claim snapshots across response loss."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b03a7b8'
down_revision = 'af296a7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('execution_recovery_claims',
        sa.Column('request_id', sa.String(36), primary_key=True),
        sa.Column('execution_id', sa.String(36), sa.ForeignKey('execution_recovery.execution_id'), nullable=False, unique=True),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('task_snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM execution_recovery_claims LIMIT 1')).first():
        raise RuntimeError('recovery claim snapshots remain; reconcile and archive before downgrade')
    op.drop_table('execution_recovery_claims')
