"""Orchestration snapshots and per-attempt service dependencies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='r841a23'
down_revision='q730f12'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('workers',sa.Column('service_network',js))
    op.add_column('workers',sa.Column('service_network_revision',sa.Integer(),nullable=False,server_default='0'))
    for name,length in [('assigned_service_instance_id',36),('assigned_service_execution_id',36),('assigned_service_key',64)]:
        op.add_column('evaluation_runs',sa.Column(name,sa.String(length)))
    op.add_column('evaluation_runs',sa.Column('waiting_service',sa.Boolean(),nullable=False,server_default='false'))
    with op.batch_alter_table('service_instances') as batch:
        batch.add_column(sa.Column('orchestration_batch_id',sa.String(36)))
        batch.create_foreign_key('fk_service_orchestration_batch','evaluation_batches',['orchestration_batch_id'],['id'])
        batch.create_index('ix_service_instances_orchestration_batch_id',['orchestration_batch_id'])
        batch.add_column(sa.Column('service_key',sa.String(64)))
        batch.add_column(sa.Column('auto_stopped',sa.Boolean(),nullable=False,server_default='false'))
    op.create_table('evaluation_orchestrations',sa.Column('batch_id',sa.String(36),sa.ForeignKey('evaluation_batches.id'),primary_key=True),
                    sa.Column('request_digest',sa.String(64),nullable=False),sa.Column('definition',js,nullable=False),
                    sa.Column('stop_after_batch',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    active=op.get_bind().execute(sa.text("""SELECT 1 FROM evaluation_runs r JOIN evaluation_jobs j ON j.id=r.job_id
        WHERE (j.batch_id IN (SELECT batch_id FROM evaluation_orchestrations)
          OR j.service_instance_id IN (SELECT id FROM service_instances WHERE orchestration_batch_id IS NOT NULL))
        AND (r.status IN ('PENDING','RESERVED','STARTING','RUNNING','CANCEL_REQUESTED') OR NOT r.cleanup_confirmed) LIMIT 1""")).first()
    if active:raise RuntimeError('stop and drain all orchestrations before database downgrade')
    op.drop_table('evaluation_orchestrations')
    with op.batch_alter_table('service_instances') as batch:
        batch.drop_index('ix_service_instances_orchestration_batch_id');batch.drop_constraint('fk_service_orchestration_batch',type_='foreignkey')
        for name in ('auto_stopped','service_key','orchestration_batch_id'):batch.drop_column(name)
    for name in ('waiting_service','assigned_service_instance_id','assigned_service_execution_id','assigned_service_key'):op.drop_column('evaluation_runs',name)
    op.drop_column('workers','service_network_revision');op.drop_column('workers','service_network')
