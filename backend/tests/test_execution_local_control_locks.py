"""Real PostgreSQL races: execution control must not serialize unrelated work."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time

import pytest
from sqlalchemy import select, text

from backend.app import recovery_records, recovery_control as control
from backend.app.batch_controls import cancel_job
from backend.app.core import Job, Run, ServiceInstance, Worker, now
from backend.app.execution_logs import append, LogChunk
from backend.app.scheduling import ControlBusy, lock, user_control_lock
from backend.tests.test_execution_logs_postgres import pg, seed
from backend.tests.test_recovery_control import apply, observation
from backend.tests.test_recovery_launch import setup, prepare


def initial(pg, service=False):
    factory = seed(pg[0])
    with factory() as s:
        run = s.get(Run, 'r')
        run.status = s.get(Job, 'j').status = 'STARTING'
        run.enqueued_at = now()
        generation = recovery_records.grant(s, run, 'w').generation
        if service:
            s.add(ServiceInstance(id='svc', name='svc', worker_id='w', slot_id='s',
                address='127.0.0.1', port=19000, current_job_id='j', template_snapshot={},
                health_ready=True, health_at=now(), probe_state={'epoch':'old', 'sequence':2}))
            s.flush()
            s.get(Job, 'j').service_instance_id = 'svc'
            s.get(Job, 'j').benchmark = 'inference-service'
        s.commit()
    return factory, generation


@pytest.mark.parametrize('service', [False, True])
def test_first_start_and_exit_observation_ignore_scheduler_lock(pg, service):
    factory, generation = initial(pg, service)
    with factory() as scheduler:
        lock(scheduler)
        assert apply(factory, generation)['action'] == 'track'
        assert apply(factory, generation, sequence=2, state='exited', exit_code=0)['action'] == 'retain'
    with factory() as s:
        assert not s.get(Run, 'r').cleanup_confirmed
        assert s.get(Run, 'r').slot_id == 's'


def test_launch_permission_ignores_scheduler_but_serializes_same_execution(pg):
    factory = seed(pg[0])
    with factory() as s:
        s.get(Run, 'r').status = 'STARTING'
        s.get(Run, 'r').enqueued_at = now()
        s.commit()
    request = setup(factory)
    with factory() as scheduler:
        lock(scheduler)
        assert prepare(factory, request)['action'] == 'launch'
        assert prepare(factory, request)['duplicate']
    with factory() as holder:
        holder.scalar(select(Run).where(Run.id == 'r').with_for_update(key_share=True))
        with pytest.raises(ControlBusy) as error:
            prepare(factory, request)
        assert error.value.reason == 'execution_lock'


def test_expired_service_keeps_readiness_guard(pg):
    factory, generation = initial(pg, True)
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = now()-timedelta(seconds=1)
        s.commit()
    with factory() as scheduler:
        lock(scheduler)
        with pytest.raises(ControlBusy) as error:
            apply(factory, generation)
        assert error.value.reason == 'scheduler_lock'
    assert apply(factory, generation)['action'] == 'track'
    with factory() as s:
        assert not s.get(ServiceInstance, 'svc').health_ready
        assert s.get(ServiceInstance, 'svc').probe_state['epoch'] is None


def test_service_expiry_between_read_and_lock_retries_without_reverse_lock_order(pg, monkeypatch):
    factory, generation = initial(pg, True)
    at = now()
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = at+timedelta(seconds=2)
        s.commit()
    times = iter([at, at+timedelta(seconds=3)])
    monkeypatch.setattr(control, 'now', lambda: next(times))
    with pytest.raises(ControlBusy) as error:
        apply(factory, generation)
    assert error.value.reason == 'execution_recheck'
    with factory() as s:
        assert s.get(ServiceInstance, 'svc').health_ready
        assert s.get(Run, 'r').status == 'STARTING'


def test_start_confirmation_lock_allows_log_foreign_key_insert(pg):
    factory, generation = initial(pg)
    with factory() as report:
        control.reconcile(report, worker_id='w', execution_id='e', observation=observation(generation))
        with factory() as logger:
            append(logger, 'e', logger.get(Worker, 'w'), LogChunk(sequence=0, content='during start'))
            logger.commit()
        report.commit()


def test_cancel_refreshes_stale_job_after_concurrent_start(pg):
    factory, generation = initial(pg)
    with factory() as cancelling, factory() as starting, ThreadPoolExecutor(1) as pool:
        job = cancelling.get(Job, 'j')
        lock(cancelling)
        control.reconcile(starting, worker_id='w', execution_id='e', observation=observation(generation))
        # Cancellation owns the global lock, startup owns the execution lock.
        pending = pool.submit(cancel_job, cancelling, job)
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            with pg[0].connect() as c:
                waiting = c.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND pid<>pg_backend_pid()"))
            if waiting:
                break
            time.sleep(.01)
        assert waiting and not pending.done()
        starting.commit()
        assert pending.result(timeout=3) == 'CANCEL_REQUESTED'
        cancelling.commit()
    assert apply(factory, generation, sequence=2)['action'] == 'stop'
    with factory() as s:
        assert s.get(Job, 'j').status == s.get(Run, 'r').status == 'CANCEL_REQUESTED'
        assert not s.get(Run, 'r').cleanup_confirmed


def test_cancel_before_first_start_keeps_platform_intent(pg):
    factory, generation = initial(pg)
    with factory() as cancelling:
        lock(cancelling)
        assert cancel_job(cancelling, cancelling.get(Job, 'j')) == 'CANCEL_REQUESTED'
        with pytest.raises(ControlBusy) as error:
            apply(factory, generation)
        assert error.value.reason == 'execution_lock'
        cancelling.commit()
    assert apply(factory, generation)['reason'] == 'cancelled'
    with factory() as s:
        assert s.get(Run, 'r').started_at is None
        assert not s.get(Run, 'r').cleanup_confirmed


def test_user_control_wait_releases_connection_and_accepts_after_scheduler(pg):
    factory = seed(pg[0])
    with factory() as scheduler, factory() as cancelling, ThreadPoolExecutor(1) as executor:
        lock(scheduler)
        future = executor.submit(user_control_lock, cancelling, 1.5)
        deadline = time.monotonic()+1
        while time.monotonic()<deadline and pg[0].pool.checkedout()>1:
            time.sleep(.01)
        assert pg[0].pool.checkedout()==1  # waiter released its checkout
        scheduler.rollback()
        future.result(timeout=2)
        assert pg[0].pool.checkedout()==1  # successful transaction owns its lock
        cancelling.rollback()
    assert pg[0].pool.checkedout()==0


def test_user_control_wait_times_out_without_leaking_connection(pg):
    factory = seed(pg[0])
    with factory() as scheduler, factory() as cancelling:
        lock(scheduler)
        with pytest.raises(ControlBusy) as error:
            user_control_lock(cancelling, .12)
        assert error.value.reason=='scheduler_lock'
        assert pg[0].pool.checkedout()==1
    assert pg[0].pool.checkedout()==0
