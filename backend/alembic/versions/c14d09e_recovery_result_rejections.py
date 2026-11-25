"""Keep original rejected recovery results with their receipt ACK."""
from alembic import op
import sqlalchemy as sa

revision = 'c14d09e'
down_revision = 'b03a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('execution_recovery_receipts', sa.Column('application_state', sa.String(20)))
    op.add_column('execution_recovery_receipts', sa.Column('rejection_reason', sa.String(1000)))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM execution_recovery_receipts WHERE application_state = 'rejected' LIMIT 1")).first():
        raise RuntimeError('rejected recovery results must be reconciled before downgrade')
    op.drop_column('execution_recovery_receipts', 'rejection_reason')
    op.drop_column('execution_recovery_receipts', 'application_state')
