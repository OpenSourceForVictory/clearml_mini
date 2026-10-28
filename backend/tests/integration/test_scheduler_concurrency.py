"""Use a dedicated PostgreSQL test database, never a production database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
import pytest
from sqlalchemy import delete, select, text
from backend.app.core import Base, Event, Job, Pool, Run, Slot, User, Worker, now, session_factory
from backend.app.scheduling import ACTIVE, tick

pytestmark = pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL is not configured')


def test_transaction_leader_and_atomic_capacity(monkeypatch):
    factory = session_factory(os.environ['TEST_DATABASE_URL'])
    suffix = uuid.uuid4().hex[:8]
    users = ['u'+suffix+str(i) for i in range(3)]
    pool, worker = 'p'+suffix, 'w'+suffix
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '3')
    try:
        with factory() as s:
            s.add_all([User(id=u, username=u, password_hash='unused') for u in users])
            s.add(Pool(id=pool, name=pool, gpu_model='virtual'))
            s.add(Worker(id=worker, node_name=worker, token_hash=uuid.uuid4().hex*2, max_concurrent=3, cpu_capacity=8, ram_gb_capacity=16))
            s.flush()
            for i in range(3):
                s.add(Slot(node_name=worker, gpu_index=i, gpu_uuid=f'mock:{worker}:{i}', pool_id=pool,
                           worker_id=worker, queue_name=f'{worker}-{i}', last_seen_at=now()))
            for owner in users:
                for _ in range(5):
                    j = Job(owner_id=owner, pool_id=pool, title='concurrent', benchmark='mock', model_ref='m', episodes=1)
                    s.add(j)
                    s.flush()
                    s.add(Run(job_id=j.id, attempt=1))
            s.commit()
        barrier = Barrier(8)
        def run_tick():
            barrier.wait()
            return tick(factory)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: run_tick(), range(8)))
        assert sum(results) == 3
        with factory() as s:
            runs = s.scalars(select(Run).join(Job).where(Job.pool_id == pool, Run.status.in_(ACTIVE))).all()
            assert len(runs) == len({r.slot_id for r in runs}) == len({r.execution_id for r in runs}) == 3
            assert {s.get(Job, r.job_id).owner_id for r in runs} == set(users)
        # A held transaction lock prevents all other scheduler instances from acting.
        with factory() as s:
            s.execute(text('SELECT pg_advisory_xact_lock(82510401)'))
            assert tick(factory) == 0
    finally:
        with factory() as s:
            ids = select(Job.id).where(Job.pool_id == pool)
            s.execute(delete(Event).where(Event.job_id.in_(ids)))
            s.execute(delete(Run).where(Run.job_id.in_(ids)))
            s.execute(delete(Job).where(Job.pool_id == pool))
            s.execute(delete(Slot).where(Slot.worker_id == worker))
            s.execute(delete(Worker).where(Worker.id == worker))
            s.execute(delete(Pool).where(Pool.id == pool))
            s.execute(delete(User).where(User.id.in_(users)))
            s.commit()
