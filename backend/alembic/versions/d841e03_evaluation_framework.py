"""Reusable suites, run concurrency, and durable episode reporting."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'd841e03'
down_revision = 'c731d02'
branch_labels = depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.add_column('client_templates', sa.Column('episode_reporting', sa.Boolean(), nullable=False, server_default='false'))
    op.create_table('evaluation_suites',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False), sa.Column('version', sa.String(100), nullable=False),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('client_templates.id'), nullable=False),
        sa.Column('cases', JSON_TYPE, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'name', 'version'))
    op.create_index('ix_evaluation_suites_owner_id', 'evaluation_suites', ['owner_id'])
    op.add_column('evaluation_batches', sa.Column('suite_id', sa.String(36), sa.ForeignKey('evaluation_suites.id')))
    op.add_column('evaluation_batches', sa.Column('max_concurrent', sa.Integer(), nullable=False, server_default='1'))
    op.create_table('episode_records',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id'), primary_key=True),
        sa.Column('episode_id', sa.Integer(), primary_key=True), sa.Column('seed', sa.Integer(), nullable=False),
        sa.Column('started', JSON_TYPE, nullable=False), sa.Column('result', JSON_TYPE),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('episode_records')
    op.drop_column('evaluation_batches', 'max_concurrent')
    op.drop_column('evaluation_batches', 'suite_id')
    op.drop_table('evaluation_suites')
    op.drop_column('client_templates', 'episode_reporting')
