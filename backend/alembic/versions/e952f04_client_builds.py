"""Versioned client source builds."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = 'e952f04'
down_revision = 'd841e03'
branch_labels = depends_on = None


def upgrade():
    op.create_table('client_builds',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False), sa.Column('version', sa.String(100), nullable=False),
        sa.Column('entrypoint', sa.String(200), nullable=False),
        sa.Column('source', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False), sa.Column('status', sa.String(20), nullable=False),
        sa.Column('claim_hash', sa.String(64)), sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.Column('log', sa.Text(), nullable=False), sa.Column('image', sa.String(500)),
        sa.Column('template_id', sa.String(36), sa.ForeignKey('client_templates.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('owner_id', 'name', 'version'))
    op.create_index('ix_client_builds_owner_id', 'client_builds', ['owner_id'])
    op.create_index('ix_client_builds_status', 'client_builds', ['status'])


def downgrade():
    op.drop_table('client_builds')
