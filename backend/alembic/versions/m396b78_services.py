"""Resource-accounted inference instances."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='m396b78'
down_revision='m285b79'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.create_table('service_instances',sa.Column('id',sa.String(36),primary_key=True),sa.Column('name',sa.String(100),nullable=False),
        sa.Column('worker_id',sa.String(36),sa.ForeignKey('workers.id'),nullable=False),sa.Column('slot_id',sa.String(36),sa.ForeignKey('gpu_slots.id'),nullable=False),
        sa.Column('address',sa.String(100),nullable=False),sa.Column('port',sa.Integer(),nullable=False),sa.Column('template_snapshot',js,nullable=False),sa.Column('environment',js,nullable=False),sa.Column('parameters',js,nullable=False,server_default='{}'),
        sa.Column('gpu_mode',sa.String(20),nullable=False),sa.Column('requested_vram_gb',sa.Integer(),nullable=False),sa.Column('current_job_id',sa.String(36)),sa.Column('desired_running',sa.Boolean(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('health_at',sa.DateTime(timezone=True)),sa.Column('health_ready',sa.Boolean(),nullable=False),sa.Column('health_error',sa.String(500)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_service_instances_worker_id','service_instances',['worker_id'])
    op.add_column('evaluation_jobs',sa.Column('target_slot_id',sa.String(36),sa.ForeignKey('gpu_slots.id')))
    op.add_column('evaluation_jobs',sa.Column('service_instance_id',sa.String(36),sa.ForeignKey('service_instances.id')))
    op.create_index('ix_evaluation_jobs_service_instance_id','evaluation_jobs',['service_instance_id'])

def downgrade():
    op.drop_index('ix_evaluation_jobs_service_instance_id',table_name='evaluation_jobs')
    op.drop_column('evaluation_jobs','service_instance_id');op.drop_column('evaluation_jobs','target_slot_id');op.drop_table('service_instances')
