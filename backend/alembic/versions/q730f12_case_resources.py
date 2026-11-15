"""Explicit task requests and reusable suite parameter defaults."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='q730f12'
down_revision='p629e01'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('evaluation_suites',sa.Column('client_defaults',js,nullable=False,server_default='{}'))
    op.add_column('evaluation_jobs',sa.Column('requested_cpu',sa.Integer()))
    op.add_column('evaluation_jobs',sa.Column('requested_ram_gb',sa.Integer()))

def downgrade():
    op.drop_column('evaluation_jobs','requested_ram_gb');op.drop_column('evaluation_jobs','requested_cpu')
    op.drop_column('evaluation_suites','client_defaults')
