"""Execution inventory and opt-in retention."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'h841c23'
down_revision = 'g730b12'
branch_labels = depends_on = None
J = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')

def upgrade():
    for name, typ, default in [('retention_revision', sa.Integer(), '0'), ('retention_enabled', sa.Boolean(), 'false'),
                               ('retention_days', sa.Integer(), '30'), ('data_scan_revision', sa.Integer(), '0')]:
        op.add_column('workers', sa.Column(name, typ, nullable=False, server_default=default))
    op.add_column('workers', sa.Column('retention_checked_at', sa.DateTime(timezone=True)))
    op.create_table('execution_data',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('worker_id', sa.String(36), sa.ForeignKey('workers.id'), nullable=False),
        sa.Column('slot_id', sa.String(36), sa.ForeignKey('gpu_slots.id'), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id')),
        sa.Column('execution_id', sa.String(36)), sa.Column('root_id', sa.String(64), nullable=False),
        sa.Column('root_path', sa.String(4096), nullable=False), sa.Column('entry_name', sa.String(255), nullable=False),
        sa.Column('generation', sa.String(36), nullable=False), sa.Column('scan_status', sa.String(20), nullable=False),
        sa.Column('reason', sa.String(1000)), sa.Column('identity', J, nullable=False),
        sa.Column('logical_bytes', sa.BigInteger()), sa.Column('allocated_bytes', sa.BigInteger()),
        sa.Column('scanned_at', sa.DateTime(timezone=True), nullable=False), sa.Column('last_complete_at', sa.DateTime(timezone=True)),
        sa.Column('last_cleanup_status', sa.String(20)), sa.UniqueConstraint('slot_id','root_id','entry_name'))
    for name in ['worker_id','slot_id','run_id']:op.create_index('ix_execution_data_'+name,'execution_data',[name])
    op.create_table('data_cleanups', sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('worker_id',sa.String(36),sa.ForeignKey('workers.id'),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id')),sa.Column('source',sa.String(20),nullable=False),
        sa.Column('policy_revision',sa.Integer()),sa.Column('policy',J),sa.Column('storage_revision',sa.Integer(),nullable=False),
        sa.Column('confirmed',sa.Boolean(),nullable=False,server_default='false'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('confirmed_at',sa.DateTime(timezone=True)))
    op.create_index('ix_data_cleanups_worker_id','data_cleanups',['worker_id'])
    op.create_table('data_cleanup_items',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('operation_id',sa.String(36),sa.ForeignKey('data_cleanups.id'),nullable=False),
        sa.Column('data_id',sa.String(36),sa.ForeignKey('execution_data.id'),nullable=False),
        sa.Column('slot_id',sa.String(36),sa.ForeignKey('gpu_slots.id'),nullable=False),
        sa.Column('snapshot',J,nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('reason',sa.String(1000)),
        sa.Column('started_at',sa.DateTime(timezone=True)),sa.Column('authorized_until',sa.DateTime(timezone=True)),
        sa.Column('finished_at',sa.DateTime(timezone=True)),sa.Column('receipt',J),sa.UniqueConstraint('operation_id','data_id'))
    for name in ['operation_id','data_id','slot_id','status']:op.create_index('ix_data_cleanup_items_'+name,'data_cleanup_items',[name])

def downgrade():
    bind=op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM data_cleanup_items WHERE status IN ('queued','running')")).scalar() or bind.execute(sa.text('SELECT count(*) FROM workers WHERE retention_enabled=true')).scalar():
        raise RuntimeError('Disable retention and reconcile pending cleanup operations before downgrade.')
    for table in ['data_cleanup_items','data_cleanups','execution_data']:op.drop_table(table)
    for name in ['retention_checked_at','data_scan_revision','retention_days','retention_enabled','retention_revision']:op.drop_column('workers',name)
