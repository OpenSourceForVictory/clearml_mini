"""Template resource contracts and immutable service resource requests."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='s952b34'
down_revision='r841a23'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('gpu_slots',sa.Column('accelerator_type',sa.String(10),nullable=False,server_default='gpu'))
    op.add_column('gpu_slots',sa.Column('accelerator_backends',js,nullable=False,server_default='[]'))
    op.add_column('client_templates',sa.Column('resource_requirements',js,nullable=True))
    op.add_column('service_instances',sa.Column('resource_sources',js,nullable=True))
    op.add_column('service_instances',sa.Column('resources',js,nullable=True))

def downgrade():
    connection=op.get_bind()
    # Old binaries cannot interpret NPU identities or preserve new resource contracts.
    for query in (
        "SELECT COUNT(*) FROM gpu_slots WHERE accelerator_type <> 'gpu'",
        "SELECT COUNT(*) FROM client_templates WHERE resource_requirements IS NOT NULL AND CAST(resource_requirements AS TEXT) <> 'null'",
        "SELECT COUNT(*) FROM service_instances WHERE resources IS NOT NULL AND CAST(resources AS TEXT) <> 'null'",
    ):
        if connection.execute(sa.text(query)).scalar():
            raise RuntimeError('resource contract data present; restore the pre-upgrade database backup instead of destructive downgrade')
    op.drop_column('gpu_slots','accelerator_backends')
    op.drop_column('gpu_slots','accelerator_type')
    op.drop_column('service_instances','resource_sources')
    op.drop_column('service_instances','resources')
    op.drop_column('client_templates','resource_requirements')
