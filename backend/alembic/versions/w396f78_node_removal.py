"""Preserve deleted node history while releasing active identities."""
from alembic import op
import sqlalchemy as sa
revision='w396f78'
down_revision='v285e67'
branch_labels=None
depends_on=None


def upgrade():
    for table in ('workers','gpu_slots'):
        op.add_column(table,sa.Column('deleted_at',sa.DateTime(timezone=True)))
    targets={'workers':{('node_name',)},'gpu_slots':{('node_name','gpu_index'),('gpu_uuid',)}}
    for table,columns in targets.items():
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table):
            if tuple(constraint['column_names']) in columns:
                op.drop_constraint(constraint['name'],table,type_='unique')
    for name,table,columns in [('uq_live_worker_name','workers',['node_name']),('uq_live_slot_index','gpu_slots',['node_name','gpu_index']),('uq_live_slot_uuid','gpu_slots',['gpu_uuid'])]:
        op.create_index(name,table,columns,unique=True,postgresql_where=sa.text('deleted_at IS NULL'),sqlite_where=sa.text('deleted_at IS NULL'))


def downgrade():
    for table in ('workers','gpu_slots'):
        if op.get_bind().execute(sa.text(f'SELECT 1 FROM {table} WHERE deleted_at IS NOT NULL LIMIT 1')).first():
            raise RuntimeError('Cannot downgrade with deleted nodes/devices; preserve and reconcile history first')
    for name,table,columns in [('uq_live_worker_name','workers',['node_name']),('uq_live_slot_index','gpu_slots',['node_name','gpu_index']),('uq_live_slot_uuid','gpu_slots',['gpu_uuid'])]:
        op.drop_index(name,table_name=table)
        op.create_unique_constraint(name+'_all',table,columns)
    for table in ('gpu_slots','workers'):op.drop_column(table,'deleted_at')
