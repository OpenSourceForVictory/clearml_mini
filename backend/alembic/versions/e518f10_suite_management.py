"""Suite drafts, immutable published versions and archiving."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'e518f10'
down_revision = 'd407e09'
branch_labels = depends_on = None


def upgrade():
    for column in (
        sa.Column('status', sa.String(20), nullable=False, server_default='published'),
        sa.Column('archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('description', sa.String(2000), nullable=False, server_default=''),
        sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False, server_default='[]'),
        sa.Column('source', sa.String(1000), nullable=False, server_default=''),
    ):
        op.add_column('evaluation_suites', column)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM evaluation_suites WHERE status <> 'published' OR archived=true")).scalar():
        raise RuntimeError('Resolve draft and archived suites before downgrade; old API cannot enforce lifecycle.')
    for name in ('source','tags','description','revision','archived','status'):
        op.drop_column('evaluation_suites', name)
