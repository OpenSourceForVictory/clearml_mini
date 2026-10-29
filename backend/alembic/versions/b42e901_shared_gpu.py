"""Heterogeneous pools and explicitly reserved GPU shares."""
from alembic import op
import sqlalchemy as sa

revision = 'b42e901'
down_revision = '8d620a2'
branch_labels = depends_on = None

FIELDS = {
    'resource_pools': [('allow_shared', sa.Boolean(), 'false'), ('max_tasks_per_gpu', sa.Integer(), '1'),
                       ('vram_reserve_gb', sa.Integer(), '2'), ('max_gpu_utilization', sa.Integer(), '90')],
    'gpu_slots': [('gpu_model', sa.String(100), None), ('vram_mb', sa.Integer(), '0'),
                  ('protocol_version', sa.Integer(), '1'), ('free_vram_mb', sa.Integer(), None),
                  ('gpu_utilization', sa.Integer(), None), ('telemetry_at', sa.DateTime(timezone=True), None)],
    'evaluation_jobs': [('gpu_mode', sa.String(20), 'exclusive'), ('requested_vram_gb', sa.Integer(), '0'),
                        ('required_gpu_model', sa.String(100), None)],
    'evaluation_runs': [('allocation_index', sa.Integer(), '0'), ('reserved_vram_mb', sa.Integer(), '0'),
                        ('exclusive', sa.Boolean(), 'true'), ('reserved_cpu', sa.Integer(), '1'),
                        ('reserved_ram_gb', sa.Integer(), '1')],
}


def upgrade():
    for table, fields in FIELDS.items():
        for name, kind, default in fields:
            op.add_column(table, sa.Column(name, kind, nullable=default is None, server_default=default))
    op.execute('UPDATE gpu_slots SET gpu_model = (SELECT gpu_model FROM resource_pools WHERE id = gpu_slots.pool_id)')
    op.execute("UPDATE resource_pools SET gpu_model = '*' WHERE gpu_model <> 'virtual'")
    op.execute('''UPDATE evaluation_runs SET reserved_cpu = (SELECT p.cpu_per_job FROM evaluation_jobs j
                  JOIN resource_pools p ON j.pool_id=p.id WHERE j.id=evaluation_runs.job_id),
                  reserved_ram_gb = (SELECT p.ram_gb_per_job FROM evaluation_jobs j
                  JOIN resource_pools p ON j.pool_id=p.id WHERE j.id=evaluation_runs.job_id)''')
    op.drop_index('uq_active_run_slot', 'evaluation_runs')
    # Historical unconfirmed runs can share a slot with a later run in old schemas.
    # Give all held rows distinct indices before creating the stronger cleanup-aware constraint.
    op.execute('''WITH numbered AS (SELECT id, ROW_NUMBER() OVER (PARTITION BY slot_id ORDER BY reserved_at, id)-1 AS idx
                  FROM evaluation_runs WHERE slot_id IS NOT NULL AND
                  (status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED') OR cleanup_confirmed=false))
                  UPDATE evaluation_runs SET allocation_index = (SELECT idx FROM numbered WHERE numbered.id=evaluation_runs.id)
                  WHERE id IN (SELECT id FROM numbered)''')
    predicate = sa.text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED') OR cleanup_confirmed = false")
    op.create_index('uq_held_run_share', 'evaluation_runs', ['slot_id', 'allocation_index'], unique=True,
                    postgresql_where=predicate, sqlite_where=predicate)


def downgrade():
    # Must drain all shared executions before downgrade; index creation fails safely otherwise.
    op.create_index('uq_active_run_slot', 'evaluation_runs', ['slot_id'], unique=True,
        postgresql_where=sa.text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"),
        sqlite_where=sa.text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"))
    op.drop_index('uq_held_run_share', 'evaluation_runs')
    for table, fields in reversed(list(FIELDS.items())):
        for name, _, _ in reversed(fields):
            op.drop_column(table, name)
