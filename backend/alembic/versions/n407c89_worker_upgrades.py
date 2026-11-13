"""Worker release metadata and managed upgrade history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='n407c89'
down_revision='m396b78'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('workers',sa.Column('upgrade_state',sa.String(20)))
    op.add_column('workers',sa.Column('upgrade_revision',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('workers',sa.Column('updater_seen_at',sa.DateTime(timezone=True)))
    op.add_column('gpu_slots',sa.Column('software',js))
    op.create_table('worker_releases',sa.Column('id',sa.String(64),primary_key=True),sa.Column('version',sa.String(100),nullable=False),sa.Column('protocol_version',sa.Integer(),nullable=False),sa.Column('capabilities',js,nullable=False),sa.Column('archive',sa.LargeBinary(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('worker_upgrades',sa.Column('id',sa.String(36),primary_key=True),sa.Column('worker_id',sa.String(36),sa.ForeignKey('workers.id'),nullable=False),sa.Column('release_id',sa.String(64),sa.ForeignKey('worker_releases.id'),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('slot_ids',js,nullable=False),sa.Column('error',sa.String(500)),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_worker_upgrades_worker_id','worker_upgrades',['worker_id'])

def downgrade():
    op.drop_table('worker_upgrades');op.drop_table('worker_releases');op.drop_column('gpu_slots','software')
    for name in ('upgrade_state','upgrade_revision','updater_seen_at'):op.drop_column('workers',name)
