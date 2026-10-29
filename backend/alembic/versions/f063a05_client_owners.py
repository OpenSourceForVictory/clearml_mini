"""Private templates for user source builds."""
from alembic import op
import sqlalchemy as sa
revision = 'f063a05'
down_revision = 'e952f04'
branch_labels = depends_on = None


def upgrade():
    op.add_column('client_templates', sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id')))
    op.create_index('ix_client_templates_owner_id', 'client_templates', ['owner_id'])


def downgrade():
    op.drop_index('ix_client_templates_owner_id', 'client_templates')
    op.drop_column('client_templates', 'owner_id')
