"""Single-session inference endpoint admission."""
from alembic import op
import sqlalchemy as sa
revision = 'b285c07'
down_revision = 'a174b06'
branch_labels = depends_on = None


def upgrade():
    op.add_column('client_templates', sa.Column('endpoint_exclusive', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    op.drop_column('client_templates', 'endpoint_exclusive')
