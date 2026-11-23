"""Separate latest Agent reports from desired Worker configuration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'ad074e5'
down_revision = 'ac963d4'
branch_labels = None
depends_on = None

OBSERVED = ('sequence', 'seen_at', 'samples', 'devices', 'programs',
            'applied_revision', 'config_status', 'events', 'event_ack')
JSON = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
workers = sa.table('workers', sa.column('id', sa.String()), sa.column('connection', JSON))
reports = sa.table('node_agent_observations', sa.column('worker_id', sa.String()),
                   sa.column('session', sa.String()), sa.column('payload', JSON))


def upgrade():
    op.create_table('node_agent_observations',
        sa.Column('worker_id', sa.String(36), sa.ForeignKey('workers.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('session', sa.String(32)),
        sa.Column('payload', JSON, nullable=False, server_default='{}'))
    bind = op.get_bind()
    for identity, connection in bind.execute(sa.select(workers.c.id, workers.c.connection)):
        agent = (connection or {}).get('agent', {})
        if agent:
            bind.execute(reports.insert().values(worker_id=identity, session=agent.get('session'),
                         payload={k:v for k,v in agent.items() if k in OBSERVED}))


def downgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.select(workers.c.id, workers.c.connection, reports.c.session, reports.c.payload)
                        .join(reports, reports.c.worker_id == workers.c.id)).all()
    for identity, connection, session, payload in rows:
        agent = (connection or {}).get('agent', {})
        if agent and agent.get('session') == session:
            bind.execute(workers.update().where(workers.c.id == identity).values(
                connection={**connection, 'agent':{**agent, **{k:v for k,v in payload.items() if k in OBSERVED}}}))
    op.drop_table('node_agent_observations')
