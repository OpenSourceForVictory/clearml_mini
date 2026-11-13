"""Optional versioned business parameter schema; legacy templates stay untyped."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'm285b79'
down_revision = 'l285a67'
branch_labels = depends_on = None

def upgrade():
    op.add_column('client_templates', sa.Column('parameter_schema', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True))

def downgrade():
    op.drop_column('client_templates', 'parameter_schema')
