"""Worker identities, leases and bounded logs.

Revision ID: 5ad720e1
Revises: 3244941b2cbf
"""
from alembic import op
import sqlalchemy as sa

revision = '5ad720e1'
down_revision = '3244941b2cbf'
branch_labels = depends_on = None


def upgrade():
    op.create_table('workers',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('node_name', sa.String(100), nullable=False, unique=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('max_concurrent', sa.Integer(), nullable=False),
        sa.Column('cpu_capacity', sa.Integer(), nullable=False),
        sa.Column('ram_gb_capacity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.add_column('resource_pools', sa.Column('cpu_per_job', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('resource_pools', sa.Column('ram_gb_per_job', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('gpu_slots', sa.Column('worker_id', sa.String(36), sa.ForeignKey('workers.id')))
    op.add_column('gpu_slots', sa.Column('quarantined', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index('ix_gpu_slots_worker_id', 'gpu_slots', ['worker_id'])
    op.add_column('evaluation_runs', sa.Column('execution_id', sa.String(36)))
    op.create_unique_constraint('uq_runs_execution_id', 'evaluation_runs', ['execution_id'])
    op.add_column('evaluation_runs', sa.Column('lease_expires_at', sa.DateTime(timezone=True)))
    op.add_column('evaluation_runs', sa.Column('cleanup_confirmed', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('evaluation_runs', sa.Column('log_bytes', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('run_logs',
        sa.Column('run_id', sa.String(36), sa.ForeignKey('evaluation_runs.id'), primary_key=True),
        sa.Column('sequence', sa.Integer(), primary_key=True),
        sa.Column('content', sa.Text(), nullable=False))


def downgrade():
    op.drop_table('run_logs')
    for name in ('log_bytes', 'cleanup_confirmed', 'lease_expires_at'):
        op.drop_column('evaluation_runs', name)
    op.drop_constraint('uq_runs_execution_id', 'evaluation_runs', type_='unique')
    op.drop_column('evaluation_runs', 'execution_id')
    op.drop_index('ix_gpu_slots_worker_id', 'gpu_slots')
    op.drop_column('gpu_slots', 'worker_id')
    op.drop_column('gpu_slots', 'quarantined')
    op.drop_column('resource_pools', 'ram_gb_per_job')
    op.drop_column('resource_pools', 'cpu_per_job')
    op.drop_table('workers')
