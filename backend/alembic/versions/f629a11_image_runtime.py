"""User image templates and versioned node runtime configuration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'f629a11'
down_revision = 'e518f10'
branch_labels = depends_on = None
JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def upgrade():
    op.add_column('client_templates', sa.Column('runtime', JSON))
    op.add_column('workers', sa.Column('runtime_revision', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('workers', sa.Column('runtime_config', JSON))
    op.add_column('gpu_slots', sa.Column('runtime_report', JSON))
    op.add_column('gpu_slots', sa.Column('runtime_reported_at', sa.DateTime(timezone=True)))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM client_templates WHERE runtime IS NOT NULL AND CAST(runtime AS TEXT) <> 'null'")).scalar() or op.get_bind().execute(sa.text("SELECT count(*) FROM workers WHERE runtime_revision > 0")).scalar():
        raise RuntimeError('Runtime templates/configurations require the new API; drain and remove them before downgrade.')
    for table, names in [('gpu_slots', ['runtime_reported_at', 'runtime_report']), ('workers', ['runtime_config', 'runtime_revision']), ('client_templates', ['runtime'])]:
        for name in names:
            op.drop_column(table, name)
