"""Generic client and server templates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='k174f56'
down_revision='j063e45'
branch_labels=depends_on=None

def upgrade():
    js=sa.JSON().with_variant(postgresql.JSONB(),'postgresql')
    op.add_column('client_templates',sa.Column('execution_mode',sa.String(20),nullable=False,server_default='docker'))
    op.add_column('client_templates',sa.Column('integration_mode',sa.String(20),nullable=False,server_default='direct'))
    op.add_column('client_templates',sa.Column('host_runtime',js))
    op.add_column('client_templates',sa.Column('archived',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_table('server_templates',sa.Column('id',sa.String(36),primary_key=True),sa.Column('name',sa.String(100),nullable=False),sa.Column('version',sa.String(100),nullable=False),sa.Column('definition',js,nullable=False),sa.Column('enabled',sa.Boolean(),nullable=False),sa.Column('archived',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('name','version'))

def downgrade():
    op.drop_table('server_templates')
    for c in ('archived','host_runtime','integration_mode','execution_mode'):op.drop_column('client_templates',c)
