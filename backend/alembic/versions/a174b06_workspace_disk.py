"""Worker workspace disk low-watermark telemetry."""
from alembic import op
import sqlalchemy as sa
revision = 'a174b06'
down_revision = 'f063a05'
branch_labels = depends_on = None


def upgrade():
    op.add_column('gpu_slots', sa.Column('disk_free_mb', sa.Integer()))
    op.add_column('gpu_slots', sa.Column('disk_min_free_mb', sa.Integer()))
    op.add_column('gpu_slots', sa.Column('disk_at', sa.DateTime(timezone=True)))


def downgrade():
    for name in ('disk_at', 'disk_min_free_mb', 'disk_free_mb'):
        op.drop_column('gpu_slots', name)
