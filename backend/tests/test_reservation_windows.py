"""Reservation deadlines must survive work performed before transaction commit."""
from datetime import timedelta

import pytest
from sqlalchemy import event, select

from backend.app import orchestration_scheduler, scheduling
from backend.app.core import Job, Run, now
from backend.tests.test_scheduling import farm, job
from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup, submit, healthy
from backend.tests.test_desired_service_instances import additional_server


def slow_tail(monkeypatch, delay, start=None):
    clock = [start or now()]
    original = orchestration_scheduler.fill_desired_instances

    def fill(session, at):
        result = original(session, at)
        clock[0] += timedelta(seconds=delay)
        return result

    monkeypatch.setattr(scheduling, 'now', lambda: clock[0])
    monkeypatch.setattr(orchestration_scheduler, 'fill_desired_instances', fill)
    monkeypatch.setenv('RESERVATION_SECONDS', '30')
    return clock


@pytest.mark.parametrize('delay', [0, 29, 30, 31, 120])
def test_new_reservation_window_excludes_scheduling_tail(farm, monkeypatch, delay):
    identity = job(farm, 'a')
    clock = slow_tail(monkeypatch, delay)
    start = clock[0]
    assert scheduling.tick(farm) == 1
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        assert run.status == 'RESERVED' and run.execution_id
        assert scheduling.utc(run.reserved_at) == start
        assert scheduling.utc(run.reservation_expires_at) == clock[0] + timedelta(seconds=30)


def test_existing_reservation_is_not_renewed(farm, monkeypatch):
    identity = job(farm, 'a')
    start = now()
    scheduling.tick(farm, start)
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        old_id, old_deadline = run.execution_id, run.reservation_expires_at
    slow_tail(monkeypatch, 31, start + timedelta(seconds=1))
    assert scheduling.tick(farm) == 0
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        assert (run.execution_id, run.reservation_expires_at) == (old_id, old_deadline)


def test_expired_reservation_gets_new_identity_and_window(farm, monkeypatch):
    identity = job(farm, 'a')
    start = now()
    scheduling.tick(farm, start)
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        old_id = run.execution_id
        run.reservation_expires_at = start - timedelta(seconds=1)
        session.commit()
    clock = slow_tail(monkeypatch, 31, start)
    assert scheduling.tick(farm) == 1
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        assert run.execution_id != old_id
        assert scheduling.utc(run.reservation_expires_at) == clock[0] + timedelta(seconds=30)


def test_explicit_tick_time_stays_deterministic(farm, monkeypatch):
    job(farm, 'a')
    clock = slow_tail(monkeypatch, 120)
    start = clock[0]
    scheduling.tick(farm, start)
    with farm() as session:
        run = session.scalar(select(Run))
        assert scheduling.utc(run.reservation_expires_at) == start + timedelta(seconds=30)


def test_failed_commit_rolls_back_window_and_next_tick_is_fresh(farm, monkeypatch):
    identity = job(farm, 'a')
    start = now()
    clock = slow_tail(monkeypatch, 31, start)

    def fail_commit(session):
        raise RuntimeError('injected commit failure')

    event.listen(farm, 'before_commit', fail_commit)
    try:
        with pytest.raises(RuntimeError, match='injected commit failure'):
            scheduling.tick(farm)
    finally:
        event.remove(farm, 'before_commit', fail_commit)
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        assert run.status == 'PENDING' and run.execution_id is None
        assert run.reservation_expires_at is None and run.slot_id is None
    clock[0] = start + timedelta(seconds=1)
    assert scheduling.tick(farm) == 1
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        assert scheduling.utc(run.reservation_expires_at) == clock[0] + timedelta(seconds=30)


def test_joint_and_additional_service_windows_keep_distinct_ttls(env, monkeypatch):
    main, _, _ = env
    *_, body = setup(env, monkeypatch)
    additional_server(env)
    body['services'][0]['instance_count'] = 2
    body['max_concurrent'] = 1
    submit(env, body)
    clock = slow_tail(monkeypatch, 31)
    assert scheduling.tick(main.SessionLocal) == 3
    with main.SessionLocal() as session:
        runs = list(session.scalars(select(Run).where(Run.status == 'RESERVED')))
        assert len(runs) == 3
        for run in runs:
            spec = session.get(Job, run.job_id).client_spec
            if run.waiting_service:
                # The wait budget is derived from the server's template.
                service = next(session.get(Job, r.job_id) for r in runs if session.get(Job, r.job_id).benchmark == 'inference-service')
                template = service.client_spec['template']
                ttl = (template.get('readiness') or template)['startup_timeout'] + scheduling.seconds('STARTUP_TIMEOUT_SECONDS', 120)
            else:
                assert spec['workload'] == 'service'
                ttl = 30
            assert scheduling.utc(run.reservation_expires_at) == clock[0] + timedelta(seconds=ttl)


@pytest.mark.parametrize('ready', [False, True])
def test_waiting_client_only_gets_new_window_when_service_becomes_ready(env, monkeypatch, ready):
    main, _, _ = env
    token, slot, _, _, body = setup(env, monkeypatch)
    body['max_concurrent'] = 1
    submit(env, body)
    scheduling.tick(main.SessionLocal)
    if ready:
        healthy(env, token, slot)
    with main.SessionLocal() as session:
        waiting = session.scalar(select(Run).where(Run.waiting_service.is_(True)))
        identity, old_deadline = waiting.id, waiting.reservation_expires_at
    clock = slow_tail(monkeypatch, 31)
    scheduling.tick(main.SessionLocal)
    with main.SessionLocal() as session:
        run = session.get(Run, identity)
        assert run.waiting_service is (not ready)
        if ready:
            assert scheduling.utc(run.reservation_expires_at) == clock[0] + timedelta(seconds=30)
        else:
            assert run.reservation_expires_at == old_deadline


@pytest.mark.parametrize('invalidated', ['cancelled', 'replaced'])
def test_finalization_does_not_touch_invalidated_window(farm, monkeypatch, invalidated):
    identity = job(farm, 'a')
    start = now()
    with farm() as session:
        session.info['scheduler_reservation_windows'] = {}
        run = session.scalar(select(Run).where(Run.job_id == identity))
        run.status = 'RESERVED'
        run.execution_id = 'first-execution'
        scheduling.reservation_deadline(session, run, start, 30)
        if invalidated == 'cancelled':
            run.status = 'CANCELLED'
        else:
            run.execution_id = 'replacement-execution'
        scheduling.finalize_reservation_windows(session, start + timedelta(seconds=31))
        assert run.reservation_expires_at == start + timedelta(seconds=30)
        assert 'scheduler_reservation_windows' not in session.info


def test_deadline_helper_outside_tick_keeps_immediate_behavior(farm):
    identity = job(farm, 'a')
    start = now()
    with farm() as session:
        run = session.scalar(select(Run).where(Run.job_id == identity))
        scheduling.reservation_deadline(session, run, start, 73)
        scheduling.finalize_reservation_windows(session, start + timedelta(seconds=31))
        assert run.reservation_expires_at == start + timedelta(seconds=73)


def test_worker_can_claim_after_slow_tick(env, monkeypatch):
    from backend.tests.test_execution import node, submit as submit_job
    from backend.app import execution
    main, client, _ = env
    token, slots = node(env)
    identity = submit_job(env)
    # Isolate the claim deadline from the independent node liveness timeout.
    monkeypatch.setenv('WORKER_TTL_SECONDS', '300')
    clock = slow_tail(monkeypatch, 31)
    monkeypatch.setattr(execution, 'now', lambda: clock[0])
    assert scheduling.tick(main.SessionLocal) == 1
    result = client.post('/api/v1/worker/slots/' + slots[0] + '/claim', headers=token)
    assert result.status_code == 200, result.text
    assert result.json()['task']['job_id'] == identity
    assert result.json()['task']['status'] == 'STARTING'


def test_scheduler_loop_recovers_after_failed_reservation_commit(farm, monkeypatch, caplog):
    identity = job(farm, 'a')
    commits = []
    observed = []
    monkeypatch.setenv('SCHEDULER_TICK_SECONDS', '2')

    def fail_once(session):
        commits.append(True)
        if len(commits) == 1:
            raise RuntimeError('reservation commit unavailable')

    class Stop:
        def is_set(self):
            return len(observed) == 2

        def wait(self, seconds):
            assert seconds == 2
            with farm() as session:
                run = session.scalar(select(Run).where(Run.job_id == identity))
                observed.append((run.status, run.execution_id))

    event.listen(farm, 'before_commit', fail_once)
    try:
        scheduling.loop(farm, Stop())
    finally:
        event.remove(farm, 'before_commit', fail_once)
    assert observed[0] == ('PENDING', None)
    assert observed[1][0] == 'RESERVED' and observed[1][1]
    assert 'scheduler tick failed' in caplog.text
    assert 'reservation commit unavailable' in caplog.text
