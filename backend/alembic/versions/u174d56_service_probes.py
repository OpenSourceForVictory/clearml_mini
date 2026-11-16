"""Service probe generation state and independent health port."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='u174d56'
down_revision='t063c45'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('service_instances',sa.Column('health_port',sa.Integer(),nullable=True))
    op.add_column('service_instances',sa.Column('probe_state',sa.JSON().with_variant(postgresql.JSONB(),'postgresql'),nullable=False,server_default='{}'))

def downgrade():
    op.drop_column('service_instances','probe_state')
    op.drop_column('service_instances','health_port')
