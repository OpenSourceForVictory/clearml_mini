from datetime import timedelta
import pytest
from sqlalchemy import select
from backend.app.core import Base, EvaluationBatch, Event, Job, Pool, Run, Slot, User, Worker, now, session_factory
from backend.app.scheduling import tick, transition


@pytest.fixture
def farm(tmp_path, monkeypatch):
    factory = session_factory(f'sqlite:///{tmp_path / "scheduling.db"}')
    Base.metadata.create_all(factory.kw['bind'])
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '3')
    with factory() as s:
        s.add_all([User(id=x, username=x, password_hash='unused') for x in ('a', 'b', 'c')])
        s.add_all([Pool(id='large', name='large', gpu_model='A100', min_vram_gb=40),
                   Pool(id='small', name='small', gpu_model='RTX4090', min_vram_gb=24)])
        s.add_all([Worker(id='single', node_name='single', token_hash='1'*64, max_concurrent=1, cpu_capacity=8, ram_gb_capacity=16),
                   Worker(id='multi', node_name='multi', token_hash='2'*64, max_concurrent=2, cpu_capacity=8, ram_gb_capacity=16)])
        s.flush()
        for node, index, pool in [('single', 0, 'small'), ('multi', 0, 'large'), ('multi', 1, 'large')]:
            s.add(Slot(id=f'{node}-{index}', node_name=node, gpu_index=index, gpu_uuid=f'GPU-{node}-{index}',
                       pool_id=pool, worker_id=node, queue_name=f'{node}-{index}', last_seen_at=now()))
        s.commit()
    return factory


def job(factory, owner, pool='large'):
    with factory() as s:
        j = Job(owner_id=owner, pool_id=pool, title='test', benchmark='mock', model_ref='test', episodes=1)
        s.add(j)
        s.flush()
        s.add(Run(job_id=j.id, attempt=1))
        s.commit()
        return j.id


def test_round_robin_fifo_across_ticks(farm, monkeypatch):
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '1')
    jobs = {u: [job(farm, u) for _ in range(3)] for u in ('a', 'b', 'c')}
    admitted = []
    for _ in range(9):
        assert tick(farm) == 1
        with farm() as s:
            r = s.scalar(select(Run).where(Run.status == 'RESERVED'))
            j = s.get(Job, r.job_id)
            admitted.append(j.owner_id)
            assert j.id == jobs[j.owner_id].pop(0)
            transition(s, r, 'SUCCEEDED')
            s.commit()
    assert admitted == ['a', 'b', 'c'] * 3


def test_batch_priority_then_creation_time_within_owner(farm, monkeypatch):
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '1')
    with farm() as s:
        old = EvaluationBatch(owner_id='a', title='old', priority=0, created_at=now()-timedelta(minutes=2))
        new = EvaluationBatch(owner_id='a', title='new', priority=4, created_at=now()-timedelta(minutes=1))
        equal_later = EvaluationBatch(owner_id='a', title='later', priority=4, created_at=now())
        s.add_all([old, new, equal_later]); s.flush()
        ids=[]
        for batch in (old, new, equal_later):
            item=Job(owner_id='a',pool_id='large',title=batch.title,benchmark='mock',model_ref='test',episodes=1,batch_id=batch.id)
            s.add(item);s.flush();s.add(Run(job_id=item.id,attempt=1));ids.append((batch.id,item.id))
        s.commit()
    ordered=[]
    for _ in range(3):
        assert tick(farm)==1
        with farm() as s:
            run=s.scalar(select(Run).where(Run.status=='RESERVED'))
            item=s.get(Job,run.job_id);ordered.append(item.batch_id)
            transition(s,run,'SUCCEEDED');run.cleanup_confirmed=True;s.commit()
    assert ordered==[ids[1][0],ids[2][0],ids[0][0]]


def test_heterogeneous_slots_node_caps_and_user_caps(farm, monkeypatch):
    monkeypatch.setenv('MAX_ACTIVE_PER_USER', '1')
    for u in ('a', 'b', 'c'):
        job(farm, u)
        job(farm, u, 'small')
    with farm() as s:
        # The two-GPU host has memory for only one job at a time.
        s.get(Worker, 'multi').ram_gb_capacity = 1
        s.commit()
    assert tick(farm) == 2
    with farm() as s:
        active = s.scalars(select(Run).where(Run.status == 'RESERVED')).all()
        assert len({r.slot_id for r in active}) == 2
        assert len({s.get(Job, r.job_id).owner_id for r in active}) == 2
        assert {s.get(Slot, r.slot_id).worker_id for r in active} == {'single', 'multi'}
        for run in active:
            assert s.get(Job, run.job_id).pool_id == s.get(Slot, run.slot_id).pool_id
    assert tick(farm) == 0


def test_offline_reservation_recovery_and_lost_worker_grace(farm):
    jid = job(farm, 'a')
    assert tick(farm) == 1
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        old = run.execution_id
        run.reservation_expires_at = now() - timedelta(seconds=1)
        s.commit()
    assert tick(farm) == 1  # Unclaimed reservation can be safely replaced.
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        assert old != run.execution_id
        run.status = s.get(Job, jid).status = 'RUNNING'
        run.started_at = now()
        run.cleanup_confirmed = False
        run.lease_expires_at = now() - timedelta(seconds=2)
        slot = s.get(Slot, run.slot_id)
        slot.last_seen_at = now() - timedelta(seconds=60)
        s.commit()
    tick(farm)
    with farm() as s:
        assert s.get(Job, jid).status == 'RUNNING'  # Heartbeat loss alone does not fail it.
    tick(farm, now()+timedelta(seconds=40))
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        assert run.status == 'TIMED_OUT'
        assert s.get(Slot, run.slot_id).quarantined
        assert not run.cleanup_confirmed


def test_skip_unschedulable_user(farm):
    with farm() as s:
        for slot in s.scalars(select(Slot).where(Slot.pool_id == 'large')):
            slot.last_seen_at = now() - timedelta(seconds=100)
        s.commit()
    blocked = job(farm, 'a')
    eligible = job(farm, 'b', 'small')
    assert tick(farm) == 1
    with farm() as s:
        assert s.get(Job, blocked).status == 'PENDING'
        assert s.get(Job, eligible).status == 'RESERVED'


def test_unconfirmed_timeout_keeps_node_resource_credit(farm):
    with farm() as s:
        s.get(Worker, 'multi').ram_gb_capacity = 1
        s.commit()
    first = job(farm, 'a')
    assert tick(farm) == 1
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == first))
        transition(s, run, 'TIMED_OUT')
        run.cleanup_confirmed = False
        s.get(Slot, run.slot_id).quarantined = True
        s.commit()
    second = job(farm, 'b')
    assert tick(farm) == 0  # The other GPU is free, but the node RAM is not confirmed free.
    with farm() as s:
        assert s.get(Job, second).status == 'PENDING'

@pytest.mark.parametrize('sample_offset,explicit,expected',[(1,False,1),(5,False,0),(-90,False,0),(1,True,0)])
def test_reports_committed_during_tick_use_read_completion_time(farm,monkeypatch,sample_offset,explicit,expected):
    from backend.app import scheduling,orchestration_scheduler
    start=now()
    job(farm,'a')
    def reconcile(s,at):
        # Model a heartbeat committed while the tick is reconciling, before its
        # candidate reads. Use the real storage/runtime freshness checks.
        for slot in s.scalars(select(Slot)):
            slot.protocol_version=5
            slot.storage_report={'revision':0,'status':'applied'}
            slot.runtime_report={'revision':0,'applied_revision':0}
            slot.storage_reported_at=slot.runtime_reported_at=start+timedelta(seconds=sample_offset)
        s.flush()
    monkeypatch.setattr(orchestration_scheduler,'reconcile',reconcile)
    times=iter([start,start+timedelta(seconds=2)])
    monkeypatch.setattr(scheduling,'now',lambda:next(times))
    assert tick(farm,start if explicit else None)==expected
