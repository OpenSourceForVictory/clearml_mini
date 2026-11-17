"""Many-to-many suite/client availability; preserve the legacy default."""
from alembic import op
import sqlalchemy as sa
revision='v285e67'
down_revision='u174d56'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('suite_client_templates',
        sa.Column('suite_id',sa.String(36),sa.ForeignKey('evaluation_suites.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('template_id',sa.String(36),sa.ForeignKey('client_templates.id'),primary_key=True))
    op.create_index('ix_suite_client_templates_template_id','suite_client_templates',['template_id'])
    op.execute('INSERT INTO suite_client_templates (suite_id,template_id) SELECT id,template_id FROM evaluation_suites')

def downgrade():
    if op.get_bind().execute(sa.text('SELECT suite_id FROM suite_client_templates GROUP BY suite_id HAVING COUNT(*) > 1 LIMIT 1')).first():
        raise RuntimeError('Cannot downgrade suites with multiple client templates; export and resolve selections first')
    op.drop_index('ix_suite_client_templates_template_id',table_name='suite_client_templates')
    op.drop_table('suite_client_templates')
