"""Enforce one active execution per GPU slot at the database boundary."""
from alembic import op
import sqlalchemy as sa
revision = '8d620a2'
down_revision = '5ad720e1'
branch_labels = depends_on = None


def upgrade():
    op.create_index('uq_active_run_slot', 'evaluation_runs', ['slot_id'], unique=True,
        postgresql_where=sa.text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"),
        sqlite_where=sa.text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED')"))


def downgrade():
    op.drop_index('uq_active_run_slot', 'evaluation_runs')
