"""Bind concrete inference addresses to execution attempts."""
from alembic import op
import sqlalchemy as sa
revision = 'i952d34'
down_revision = 'h841c23'
branch_labels = depends_on = None


def upgrade():
    op.add_column('evaluation_runs', sa.Column('assigned_endpoint', sa.String(2000)))
    op.add_column('evaluation_runs', sa.Column('assigned_endpoint_mode', sa.String(10)))
    op.create_check_constraint('ck_run_endpoint_pair', 'evaluation_runs',
        "(assigned_endpoint IS NULL AND assigned_endpoint_mode IS NULL) OR (assigned_endpoint IS NOT NULL AND assigned_endpoint_mode IS NOT NULL AND assigned_endpoint_mode IN ('exclusive','shared'))")
    op.execute("""UPDATE evaluation_runs r SET assigned_endpoint=j.client_spec->>'endpoint',
        assigned_endpoint_mode=CASE WHEN (j.client_spec->'template'->>'endpoint_exclusive')::boolean IS TRUE THEN 'exclusive' ELSE 'shared' END
        FROM evaluation_jobs j WHERE r.job_id=j.id AND r.status <> 'PENDING' AND r.execution_id IS NOT NULL
        AND (r.status <> 'CANCELLED' OR r.started_at IS NOT NULL)
        AND j.client_spec->>'endpoint' IS NOT NULL""")


def downgrade():
    if op.get_bind().execute(sa.text("""SELECT count(*) FROM evaluation_runs r JOIN evaluation_jobs j ON j.id=r.job_id
        WHERE j.client_spec->'endpoints' IS NOT NULL AND
        (r.status IN ('PENDING','RESERVED','STARTING','RUNNING','CANCEL_REQUESTED') OR r.cleanup_confirmed=false)""")).scalar():
        raise RuntimeError('Drain multi-address tasks and confirm cleanup before downgrade; preserve historical snapshots for rollback.')
    op.drop_constraint('ck_run_endpoint_pair', 'evaluation_runs', type_='check')
    op.drop_column('evaluation_runs', 'assigned_endpoint_mode')
    op.drop_column('evaluation_runs', 'assigned_endpoint')
