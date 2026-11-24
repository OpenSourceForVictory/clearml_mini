"""Real reservation/ORM tests; all nodes and databases are isolated fixtures."""
from datetime import timedelta
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_orchestrations import setup, submit
from backend.app.core import (Slot, Worker, EvaluationBatch, ServiceInstance,
                              Job, SchedulerState, User, now)
from backend.app.scheduling import tick
from backend.app.orchestration_scheduler import fill_desired_instances


def add_server(env, name):
    _, ids = node(env, name)
    with env[0].SessionLocal() as s:
        source = s.scalar(select(Slot).join(Worker).where(Worker.node_name == 'server'))
        slot = s.get(Slot, ids[0])
        for key in ('protocol_version', 'client_images', 'storage_report',
                    'storage_reported_at', 'runtime_report', 'runtime_reported_at'):
            setattr(slot, key, getattr(source, key))
        worker = s.get(Worker, slot.worker_id)
        worker.roles = ['server']; worker.max_concurrent = 4
        worker.service_network = {'address': '192.0.2.3', 'port_start': 19000, 'port_end': 19002}
        s.commit()
    return ids[0]


def prepare(env, monkeypatch):
    *_, body = setup(env, monkeypatch)
    add_server(env, 'server-second')
    body['cases'] = body['cases'][:1]
    body['max_concurrent'] = 1
    body['services'][0]['instance_count'] = 3
    batches = sorted(submit(env, body, key=str(i))['id'] for i in range(2))
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '4')
    assert tick(env[0].SessionLocal) == 4
    add_server(env, 'server-third')
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '5')
    with env[0].SessionLocal() as s:
        at = now()
        for identity in batches: s.get(EvaluationBatch, identity).created_at = at
        s.commit()
    return batches


def counts(s, ids):
    return [len(list(s.scalars(select(ServiceInstance).where(
        ServiceInstance.orchestration_batch_id == identity)))) for identity in ids]


@pytest.mark.parametrize('order', ['priority', 'created', 'tie'])
def test_refill_orders_within_owner(env, monkeypatch, order):
    low, high = prepare(env, monkeypatch)
    with env[0].SessionLocal() as s:
        a, b = s.get(EvaluationBatch, low), s.get(EvaluationBatch, high)
        a.created_at = b.created_at = now()
        if order == 'priority': b.priority = 9
        if order == 'created': b.created_at -= timedelta(seconds=10)
        s.commit()
    assert tick(env[0].SessionLocal) == 1
    with env[0].SessionLocal() as s:
        assert counts(s, [low, high]) == ([2, 1] if order == 'tie' else [1, 2])
        cursor = s.get(SchedulerState, 'service_rotation')
        assert cursor.value == {'owner': a.owner_id}
    assert tick(env[0].SessionLocal) == 0


def split_owners(env, ids):
    with env[0].SessionLocal() as s:
        users = sorted(s.scalars(select(User).where(User.username.in_(['alice', 'bob']))), key=lambda u:u.id)
        for identity, user in zip(ids, users):
            s.get(EvaluationBatch, identity).owner_id = user.id
            service_jobs = list(s.scalars(select(ServiceInstance.current_job_id).where(ServiceInstance.orchestration_batch_id == identity)))
            for job in s.scalars(select(Job).where((Job.batch_id == identity) | Job.id.in_(service_jobs))):
                job.owner_id = user.id
        s.commit()
        return [u.id for u in users]


def test_rotation_survives_new_session_and_priority_does_not_cross_owner(env, monkeypatch):
    ids = prepare(env, monkeypatch); owners = split_owners(env, ids)
    with env[0].SessionLocal() as s:
        s.get(EvaluationBatch, ids[0]).priority = 9
        s.add(SchedulerState(key='service_rotation', value={'owner': owners[0]}))
        s.commit()
    assert tick(env[0].SessionLocal) == 1
    with env[0].SessionLocal() as s:
        assert counts(s, ids) == [1, 2]
        assert s.get(SchedulerState, 'service_rotation').value['owner'] == owners[1]
    add_server(env, 'server-fourth'); monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '6')
    assert tick(env[0].SessionLocal) == 1
    with env[0].SessionLocal() as s:
        assert counts(s, ids) == [2, 2]
        assert s.get(SchedulerState, 'service_rotation').value['owner'] == owners[0]


def test_one_per_owner_per_round(env, monkeypatch):
    ids = prepare(env, monkeypatch); split_owners(env, ids)
    add_server(env, 'server-fourth'); monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '6')
    assert tick(env[0].SessionLocal) == 2
    with env[0].SessionLocal() as s: assert counts(s, ids) == [2, 2]


@pytest.mark.parametrize('blocked', ['paused', 'deleted', 'resource', 'inactive'])
def test_blocked_head_does_not_block_next_candidate(env, monkeypatch, blocked):
    ids = prepare(env, monkeypatch)
    if blocked == 'inactive': split_owners(env, ids)
    with env[0].SessionLocal() as s:
        b = s.get(EvaluationBatch, ids[0]); b.priority = 9
        if blocked == 'paused': b.scheduling_paused = True
        if blocked == 'deleted': b.deleted_at = now()
        if blocked == 'inactive': s.get(User,b.owner_id).active = False
        if blocked == 'resource':
            from backend.app.core import EvaluationOrchestration
            import copy
            o = s.get(EvaluationOrchestration, ids[0]); d = copy.deepcopy(o.definition)
            d['services'][0]['slot_id'] = 'unavailable'; o.definition = d
        s.commit()
    with env[0].SessionLocal() as s:
        assert fill_desired_instances(s, now()) == 1
        assert counts(s, ids) == [1, 2]
        s.commit()


def test_refill_and_cursor_roll_back_together(env, monkeypatch):
    ids = prepare(env, monkeypatch)
    with env[0].SessionLocal() as s:
        assert fill_desired_instances(s, now()) == 1
        s.flush(); assert counts(s, ids) == [2, 1]
        s.rollback()
    with env[0].SessionLocal() as s:
        assert counts(s, ids) == [1, 1]
        assert s.get(SchedulerState, 'service_rotation') is None
        assert fill_desired_instances(s, now()) == 1
        s.commit()
