"""Persist reconciliation decisions without changing immutable recovery grants."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'af296a7'
down_revision = 'ae185f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('execution_recovery_controls',
        sa.Column('execution_id', sa.String(36), sa.ForeignKey('execution_recovery.execution_id'), primary_key=True),
        sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False))


def downgrade():
    if op.get_bind().execute(sa.text('SELECT 1 FROM execution_recovery_controls LIMIT 1')).first():
        raise RuntimeError('recovery control decisions remain; reconcile and archive before downgrade')
    op.drop_table('execution_recovery_controls')
