"""Add priority for queued evaluation batches."""
from alembic import op
import sqlalchemy as sa

revision = 'aa741b2'
down_revision = 'c952f34'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('evaluation_batches') as batch:
        batch.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='0'))
        batch.create_check_constraint('ck_evaluation_batch_priority_range',
                                      'priority >= 0 AND priority <= 9')


def downgrade():
    with op.batch_alter_table('evaluation_batches') as batch:
        batch.drop_constraint('ck_evaluation_batch_priority_range', type_='check')
        batch.drop_column('priority')
