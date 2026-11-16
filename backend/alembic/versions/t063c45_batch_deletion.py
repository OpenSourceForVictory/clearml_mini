"""Logical batch deletion keeps execution history and idempotency keys."""
from alembic import op
import sqlalchemy as sa
revision = 't063c45'
down_revision = 's952b34'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('evaluation_batches', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('evaluation_batches', 'deleted_at')
