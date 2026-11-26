"""Bound scheduler reads without changing allocation or transaction semantics."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core import Job, Run, Slot, Worker
from scripts.scheduler_benchmark import seed, measure
from backend.tests.test_execution_logs_postgres import pg


@pytest.mark.parametrize('pending', [0, 10])
def test_unrelated_history_does_not_expand_tick_objects(tmp_path, pending):
    samples = []
    for history in (0, 200):
        engine = create_engine('sqlite:///'+str(tmp_path/f'{history}.db'))
        try:
            at = seed(engine, history, pending)
            samples.append(measure(sessionmaker(bind=engine), at))
        finally:
            engine.dispose()
    assert samples[0]['admitted'] == samples[1]['admitted'] == bool(pending)
    assert samples[0]['objects_loaded'] == samples[1]['objects_loaded']
    assert samples[0]['sql_count'] == samples[1]['sql_count']


def active_sample(engine, count):
    at = seed(engine, 0, count)
    with Session(engine) as s:
        s.get(Worker, 'bench-worker').max_concurrent = count
        for i, run in enumerate(s.scalars(select(Run)).all()):
            slot_id = 'bench-slot' if i == 0 else 'active-slot-'+str(i)
            if i:
                s.add(Slot(id=slot_id, node_name='bench-worker', gpu_index=i,
                    queue_name=slot_id, worker_id='bench-worker', pool_id='bench-pool', last_seen_at=at))
                s.flush()
            run.status = s.get(Job, run.job_id).status = 'RUNNING'
            run.slot_id = slot_id
            run.execution_id = 'active-'+str(i)
            run.started_at = run.enqueued_at = at
            run.lease_expires_at = at+timedelta(minutes=5)
            run.cleanup_confirmed = False
        s.commit()
    return measure(sessionmaker(bind=engine), at)


def test_active_jobs_do_not_add_per_job_queries(tmp_path):
    samples = []
    for count in (1, 100):
        engine = create_engine('sqlite:///'+str(tmp_path/f'{count}.db'))
        try:
            samples.append(active_sample(engine, count))
        finally:
            engine.dispose()
    assert samples[0]['admitted'] == samples[1]['admitted'] == 0
    assert samples[0]['sql_count'] == samples[1]['sql_count']


def test_postgres_active_jobs_batched_and_unmodified(pg):
    result = active_sample(pg[0], 100)
    assert result['admitted'] == 0
    assert result['sql_count'] < 30
    with Session(pg[0]) as s:
        runs = s.scalars(select(Run)).all()
        assert len(runs) == 100
        assert all(r.status == 'RUNNING' and not r.cleanup_confirmed for r in runs)
        assert len({r.execution_id for r in runs}) == 100
