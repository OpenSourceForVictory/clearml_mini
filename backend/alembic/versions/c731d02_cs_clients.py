"""Client/server simulation protocol and bounded run artifacts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'c731d02'
down_revision = 'b42e901'
branch_labels = depends_on = None
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.create_table('evaluation_batches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False), sa.Column('idempotency_key', sa.String(200)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('owner_id', 'idempotency_key'))
    op.create_index('ix_evaluation_batches_owner_id', 'evaluation_batches', ['owner_id'])
    op.add_column('evaluation_jobs', sa.Column('batch_id', sa.String(36), sa.ForeignKey('evaluation_batches.id'), nullable=True))
    op.add_column('evaluation_jobs', sa.Column('case_id', sa.String(100), nullable=True))
    op.create_index('ix_evaluation_jobs_batch_id', 'evaluation_jobs', ['batch_id'])
    op.add_column('gpu_slots', sa.Column('client_images', JSON_TYPE, nullable=False, server_default='[]'))
    op.add_column('evaluation_jobs', sa.Column('client_spec', JSON_TYPE, nullable=True))
    op.create_table('client_templates',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False), sa.Column('version', sa.String(100), nullable=False),
        sa.Column('image', sa.String(500), nullable=False), sa.Column('command', JSON_TYPE, nullable=False),
        sa.Column('protocols', JSON_TYPE, nullable=False), sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('name', 'version'))
    op.create_table('run_artifacts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id'), nullable=False),
        sa.Column('name', sa.String(180), nullable=False), sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False), sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('run_id', 'name'))
    op.create_index('ix_run_artifacts_run_id', 'run_artifacts', ['run_id'])


def downgrade():
    op.drop_index('ix_evaluation_jobs_batch_id', 'evaluation_jobs')
    op.drop_column('evaluation_jobs', 'batch_id')
    op.drop_column('evaluation_jobs', 'case_id')
    op.drop_table('evaluation_batches')
    op.drop_table('run_artifacts')
    op.drop_table('client_templates')
    op.drop_column('evaluation_jobs', 'client_spec')
    op.drop_column('gpu_slots', 'client_images')
