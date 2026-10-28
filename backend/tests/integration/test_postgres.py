import os
from datetime import timezone
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from backend.app.core import Job, Pool, Run, User, now

DATABASE_URL = os.getenv('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason='TEST_DATABASE_URL is not configured')


def test_migrated_postgres_schema_and_constraints():
    engine = create_engine(DATABASE_URL)
    tables = set(inspect(engine).get_table_names())
    assert {'users', 'resource_pools', 'gpu_slots', 'evaluation_jobs', 'evaluation_runs', 'job_events', 'scheduler_state', 'alembic_version'} <= tables
    assert 'JSONB' in str(next(c for c in inspect(engine).get_columns('evaluation_runs') if c['name'] == 'result_json')['type'])
    with Session(engine) as s:
        owner = User(username='migration-test', password_hash='test')
        pool = Pool(name='migration-test', gpu_model='virtual')
        s.add_all([owner, pool])
        s.flush()
        job = Job(owner_id=owner.id, pool_id=pool.id, title='test', benchmark='mock', model_ref='m', episodes=1, idempotency_key='once')
        s.add(job)
        s.flush()
        s.add(Run(job_id=job.id, attempt=1))
        s.flush()
        assert job.created_at.tzinfo == timezone.utc
        with pytest.raises(IntegrityError):
            with s.begin_nested():
                s.add(Job(owner_id=owner.id, pool_id=pool.id, title='duplicate', benchmark='mock', model_ref='m', episodes=1, idempotency_key='once'))
                s.flush()
        s.rollback()
