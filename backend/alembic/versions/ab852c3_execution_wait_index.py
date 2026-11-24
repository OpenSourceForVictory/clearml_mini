"""Bounded waiting-start lookups, compatible with older application versions."""
from alembic import op
import sqlalchemy as sa

revision = 'ab852c3'
down_revision = 'aa741b2'
branch_labels = None
depends_on = None
NAME = 'ix_job_events_run_status_created'


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            # An interrupted concurrent build leaves an invalid index; retry it.
            valid = bind.execute(sa.text('SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass(:name)'), {'name': NAME}).scalar()
            if valid is False:op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {NAME}')
            op.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {NAME} ON job_events (run_id, new_status, created_at)')
    else:
        op.execute(f'CREATE INDEX IF NOT EXISTS {NAME} ON job_events (run_id, new_status, created_at)')


def downgrade():
    # Downgrade runs during a drained maintenance window. Keep the drop in
    # the migration transaction: a later guard must roll back both the index
    # and version record, otherwise upgrade head cannot restore the index.
    op.execute(f'DROP INDEX IF EXISTS {NAME}')
