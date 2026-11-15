"""Typed task input and ordinary command templates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='o518d90'
down_revision='n407c89'
branch_labels=depends_on=None


def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('client_templates',sa.Column('task_type',sa.String(20),nullable=False,server_default='evaluation'))
    op.add_column('client_templates',sa.Column('platform_bindings',js,nullable=False,server_default='[]'))


def downgrade():
    op.drop_column('client_templates','platform_bindings')
    op.drop_column('client_templates','task_type')
