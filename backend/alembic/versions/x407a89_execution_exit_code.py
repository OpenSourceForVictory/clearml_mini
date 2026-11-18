"""Retain observed execution exit codes independently of protocol results."""
from alembic import op
import sqlalchemy as sa
revision='x407a89'
down_revision='w396f78'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('evaluation_runs',sa.Column('exit_code',sa.Integer(),nullable=True))

def downgrade():
    op.drop_column('evaluation_runs','exit_code')
