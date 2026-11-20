"""Optional client browse directory and immutable execution path snapshot."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='c952f34'
down_revision='b841e23'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('client_templates',sa.Column('result_directory',sa.String(2000),nullable=True))
    op.create_table('execution_browse_roots',
        sa.Column('run_id',sa.String(36),sa.ForeignKey('evaluation_runs.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('execution_id',sa.String(36),nullable=False),
        sa.Column('snapshot',sa.JSON().with_variant(JSONB,'postgresql'),nullable=False))


def downgrade():
    op.drop_table('execution_browse_roots')
    op.drop_column('client_templates','result_directory')
