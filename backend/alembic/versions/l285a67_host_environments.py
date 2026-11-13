"""Approved host environments and readiness reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='l285a67'
down_revision='k174f56'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('workers',sa.Column('host_revision',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('workers',sa.Column('host_environments',js))
    op.add_column('gpu_slots',sa.Column('execution_modes',js,nullable=False,server_default='["docker"]'))
    op.add_column('gpu_slots',sa.Column('host_report',js))
    op.add_column('gpu_slots',sa.Column('host_reported_at',sa.DateTime(timezone=True)))

def downgrade():
    op.drop_column('gpu_slots','execution_modes')
    op.drop_column('gpu_slots','host_reported_at');op.drop_column('gpu_slots','host_report')
    op.drop_column('workers','host_environments');op.drop_column('workers','host_revision')
