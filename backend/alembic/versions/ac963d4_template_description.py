"""Optional user-facing client template description; server definitions use JSON."""
from alembic import op
import sqlalchemy as sa

revision = 'ac963d4'
down_revision = 'ab852c3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('client_templates', sa.Column('description', sa.String(2000), nullable=False, server_default=''))


def downgrade():
    op.drop_column('client_templates', 'description')
