"""Versioned machine storage and per-slot acknowledgements."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'c396d08'
down_revision = 'b285c07'
branch_labels = depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.add_column('workers', sa.Column('storage_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workers', sa.Column('storage_config', json_type))
    op.add_column('gpu_slots', sa.Column('storage_applied_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('gpu_slots', sa.Column('storage_report', json_type))
    op.add_column('gpu_slots', sa.Column('storage_reported_at', sa.DateTime(timezone=True)))


def downgrade():
    for name in ('storage_reported_at', 'storage_report', 'storage_applied_revision'):
        op.drop_column('gpu_slots', name)
    for name in ('storage_config', 'storage_revision'):
        op.drop_column('workers', name)
