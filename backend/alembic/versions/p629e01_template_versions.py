"""Editable private template drafts and member access policy."""
from alembic import op
import sqlalchemy as sa

revision='p629e01'
down_revision='o518d90'
branch_labels=depends_on=None


def upgrade():
    for table in ('client_templates','server_templates'):
        op.add_column(table,sa.Column('status',sa.String(20),nullable=False,server_default='published'))
        op.add_column(table,sa.Column('revision',sa.Integer(),nullable=False,server_default='0'))
    with op.batch_alter_table('server_templates') as batch:
        batch.add_column(sa.Column('owner_id',sa.String(36),nullable=True))
        batch.create_foreign_key('fk_server_templates_owner','users',['owner_id'],['id'])
        batch.create_index('ix_server_templates_owner_id',['owner_id'])
        batch.add_column(sa.Column('available_to_members',sa.Boolean(),nullable=False,server_default='false'))


def downgrade():
    with op.batch_alter_table('server_templates') as batch:
        batch.drop_index('ix_server_templates_owner_id')
        batch.drop_constraint('fk_server_templates_owner',type_='foreignkey')
        batch.drop_column('owner_id')
        batch.drop_column('available_to_members')
    for table in ('client_templates','server_templates'):
        op.drop_column(table,'revision');op.drop_column(table,'status')
