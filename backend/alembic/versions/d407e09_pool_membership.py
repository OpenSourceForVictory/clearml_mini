"""Server-managed, drainable GPU pool membership."""
from alembic import op
import sqlalchemy as sa

revision = 'd407e09'
down_revision = 'c396d08'
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table('gpu_slots') as b:
        b.alter_column('pool_id', existing_type=sa.String(36), nullable=True)
        b.add_column(sa.Column('desired_pool_id', sa.String(36), nullable=True))
        b.create_foreign_key('fk_slot_desired_pool', 'resource_pools', ['desired_pool_id'], ['id'])
        b.add_column(sa.Column('membership_revision', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('membership_applied_revision', sa.Integer(), nullable=False, server_default='0'))
        b.add_column(sa.Column('membership_updated_at', sa.DateTime(timezone=True)))
    op.execute('UPDATE gpu_slots SET desired_pool_id = pool_id')
    op.create_table('membership_audits',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('slot_id', sa.String(36), sa.ForeignKey('gpu_slots.id'), nullable=False),
        sa.Column('old_pool_id', sa.String(36), sa.ForeignKey('resource_pools.id')),
        sa.Column('target_pool_id', sa.String(36), sa.ForeignKey('resource_pools.id')),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_membership_audits_slot_id', 'membership_audits', ['slot_id'])


def downgrade():
    n = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM gpu_slots WHERE pool_id IS NULL OR membership_revision <> membership_applied_revision')).scalar()
    if n:
        raise RuntimeError('Assign unassigned GPUs and finish membership drains before downgrade; preserve historical devices.')
    op.drop_table('membership_audits')
    with op.batch_alter_table('gpu_slots') as b:
        b.drop_constraint('fk_slot_desired_pool', type_='foreignkey')
        for name in ('desired_pool_id', 'membership_revision', 'membership_applied_revision', 'membership_updated_at'):
            b.drop_column(name)
        b.alter_column('pool_id', existing_type=sa.String(36), nullable=False)
