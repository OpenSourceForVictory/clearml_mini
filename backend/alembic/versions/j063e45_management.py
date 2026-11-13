"""Machine roles and non-destructive administration.

Revision ID: j063e45
Revises: i952d34
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'j063e45'
down_revision = 'i952d34'
branch_labels = depends_on = None

def upgrade():
    js = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.add_column('workers', sa.Column('roles', js, nullable=False, server_default='["client"]'))
    op.add_column('workers', sa.Column('revoked_at', sa.DateTime(timezone=True)))
    op.add_column('workers', sa.Column('management_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workers', sa.Column('connection', js))
    op.add_column('resource_pools', sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('management_audits', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('action', sa.String(80), nullable=False), sa.Column('target_id', sa.String(36), nullable=False),
        sa.Column('details', js, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('management_audits')
    op.drop_column('resource_pools', 'archived')
    for column in ('connection', 'management_revision', 'revoked_at', 'roles'): op.drop_column('workers', column)
