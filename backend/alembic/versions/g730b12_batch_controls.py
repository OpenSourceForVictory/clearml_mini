"""Persistent batch scheduling controls."""
from alembic import op
import sqlalchemy as sa
revision = 'g730b12'
down_revision = 'f629a11'
branch_labels = depends_on = None


def upgrade():
    op.add_column('evaluation_batches', sa.Column('scheduling_paused', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('evaluation_batches', sa.Column('control_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('evaluation_batches', sa.Column('control_updated_at', sa.DateTime(timezone=True)))


def downgrade():
    bind = op.get_bind()
    held = bind.execute(sa.text("SELECT count(*) FROM evaluation_runs WHERE status IN ('RESERVED','STARTING','RUNNING','CANCEL_REQUESTED') OR cleanup_confirmed=false")).scalar()
    paused = bind.execute(sa.text("SELECT count(*) FROM evaluation_jobs j JOIN evaluation_batches b ON b.id=j.batch_id WHERE b.scheduling_paused=true AND j.status='PENDING'")).scalar()
    if held or paused:
        raise RuntimeError('Drain executions and paused pending batches before downgrading batch controls.')
    for name in ('control_updated_at', 'control_revision', 'scheduling_paused'):
        op.drop_column('evaluation_batches', name)
