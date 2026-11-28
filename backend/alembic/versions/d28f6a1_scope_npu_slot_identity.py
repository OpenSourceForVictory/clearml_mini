"""Scope live Ascend PCI identities to their node, retaining global GPU UUIDs."""
from alembic import op
import sqlalchemy as sa

revision = 'd28f6a1'
down_revision = 'c14d09e'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('uq_live_slot_uuid', table_name='gpu_slots')
    op.create_index('uq_live_gpu_uuid', 'gpu_slots', ['gpu_uuid'], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL AND accelerator_type = 'gpu'"),
                    sqlite_where=sa.text("deleted_at IS NULL AND accelerator_type = 'gpu'"))
    op.create_index('uq_live_npu_node_uuid', 'gpu_slots', ['node_name', 'gpu_uuid'], unique=True,
                    postgresql_where=sa.text("deleted_at IS NULL AND accelerator_type = 'npu'"),
                    sqlite_where=sa.text("deleted_at IS NULL AND accelerator_type = 'npu'"))


def downgrade():
    duplicate = op.get_bind().execute(sa.text('''
        SELECT gpu_uuid FROM gpu_slots WHERE deleted_at IS NULL AND gpu_uuid IS NOT NULL
        GROUP BY gpu_uuid HAVING COUNT(*) > 1 LIMIT 1
    ''')).first()
    if duplicate:
        raise RuntimeError('Cannot restore global UUID uniqueness while live devices share an identity')
    op.drop_index('uq_live_npu_node_uuid', table_name='gpu_slots')
    op.drop_index('uq_live_gpu_uuid', table_name='gpu_slots')
    op.create_index('uq_live_slot_uuid', 'gpu_slots', ['gpu_uuid'], unique=True,
                    postgresql_where=sa.text('deleted_at IS NULL'),
                    sqlite_where=sa.text('deleted_at IS NULL'))
