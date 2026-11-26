"""Execution-local writes must not contend with unrelated global scheduling."""
from datetime import timedelta
import base64

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.tests.test_db_budget import online, pg
from backend.tests.test_execution_logs_postgres import seed
from backend.app.core import Run, Job, Slot, now
from backend.app.scheduling import lock, reconcile


@pytest.mark.parametrize('operation', ['heartbeat', 'logs', 'episodes', 'artifacts', 'task-result'])
def test_execution_reports_ignore_unrelated_scheduler_lock(online, pg, operation):
    main, client, headers = online
    with main.SessionLocal() as s:
        job = s.get(Job, 'j')
        job.client_spec = {'seed': 1, 'template': {'integration_mode': 'sdk-task-result', 'task_type': 'command'}}
        s.commit()
    requests = {
        'heartbeat': ('/api/v1/worker/heartbeat', {'slot_id': 's', 'execution_id': 'e', 'started': True}),
        'logs': ('/api/v1/worker/executions/e/logs', {'sequence': 0, 'content': 'hello'}),
        'episodes': ('/api/v1/worker/executions/e/episodes', {'episode_id': 1, 'seed': 1, 'status': 'STARTED'}),
        'artifacts': ('/api/v1/worker/executions/e/artifacts', {'name': 'result.txt', 'content_base64': base64.b64encode(b'ok').decode()}),
        'task-result': ('/api/v1/worker/executions/e/task-result', {'success': True}),
    }
    with Session(pg[0]) as holder:
        lock(holder)
        path, body = requests[operation]
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert client.post(path, headers=headers, json=body).status_code == 200


@pytest.mark.parametrize("started", [False, True])
def test_start_transition_ignores_unrelated_global_lock(online, started):
    main, client, headers = online
    with main.SessionLocal() as s:
        s.get(Run, 'r').status = s.get(Job, 'j').status = 'STARTING'
        s.commit()
    # Use a separate engine so the reserved HTTP pool is not exhausted.
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    engine = create_engine(main.SessionLocal.kw['bind'].url)
    try:
        with sessionmaker(engine)() as holder:
            lock(holder)
            response = client.post('/api/v1/worker/heartbeat', headers=headers,
                                   json={'slot_id': 's', 'execution_id': 'e', 'started': started})
            assert response.status_code == 200, response.text
            with main.SessionLocal() as check:
                run = check.get(Run, 'r')
                assert run.status == check.get(Job, 'j').status == ('RUNNING' if started else 'STARTING')
                assert not run.cleanup_confirmed
                assert run.slot_id == 's'
    finally:
        engine.dispose()


def test_idle_claim_skips_global_lock_but_candidate_defers(online, pg):
    main, client, headers = online
    with main.SessionLocal() as session:
        session.get(Slot, 's').last_seen_at = now()
        session.commit()
    path = '/api/v1/worker/slots/s/claim'
    with Session(pg[0]) as holder:
        lock(holder)
        idle = client.post(path, headers=headers, json={'known_execution_ids': ['e']})
        assert idle.status_code == 200
        assert idle.json() == {'task': None, 'cleanup': []}
        busy = client.post(path, headers=headers)
        assert busy.status_code == 200
        assert busy.json() == {'task': None, 'cleanup': []}
    claimed = client.post(path, headers=headers)
    assert claimed.status_code == 200
    assert claimed.json()['task']['execution_id'] == 'e'


@pytest.mark.parametrize("initial_status", ["STARTING", "RUNNING"])
def test_reconcile_skips_locked_run_and_reclaims_after_release(pg, initial_status):
    factory = seed(pg[0])
    at = now()
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = at - timedelta(seconds=31)
        s.get(Run, 'r').status = initial_status
        s.get(Run, 'r').enqueued_at = at - timedelta(seconds=121)
        s.commit()
    with factory() as uploader, factory() as scheduler:
        uploader.scalar(select(Run).where(Run.id == 'r').with_for_update())
        lock(scheduler)
        reconcile(scheduler, at)
        scheduler.commit()
        assert scheduler.get(Run, 'r').status == initial_status
    with factory() as scheduler:
        lock(scheduler)
        reconcile(scheduler, at)
        scheduler.commit()
        assert scheduler.get(Run, 'r').status == 'TIMED_OUT'
        assert scheduler.get(Slot, 's').quarantined


def test_reconcile_refreshes_stale_lease_snapshot(pg):
    factory = seed(pg[0])
    at = now()
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = at - timedelta(seconds=31)
        s.commit()
    with factory() as scheduler, factory() as heartbeat:
        stale = scheduler.get(Run, 'r')
        heartbeat.get(Run, 'r').lease_expires_at = at + timedelta(seconds=30)
        heartbeat.commit()
        lock(scheduler)
        reconcile(scheduler, at)
        scheduler.commit()
        assert stale.status == 'RUNNING'
        assert stale.lease_expires_at > at


@pytest.mark.parametrize('state', ['CANCEL_REQUESTED', 'TIMED_OUT'])
def test_renewal_preserves_cancel_and_terminal_state(online, state):
    main, client, headers = online
    with main.SessionLocal() as s:
        s.get(Run, 'r').status = state
        s.commit()
    response = client.post('/api/v1/worker/heartbeat', headers=headers,
                           json={'slot_id': 's', 'execution_id': 'e', 'started': True})
    assert response.status_code == (200 if state == 'CANCEL_REQUESTED' else 409)
    if state == 'CANCEL_REQUESTED':
        assert response.json()['cancel'] is True
    with main.SessionLocal() as s:
        assert s.get(Run, 'r').status == state


def test_execution_renewal_does_not_wait_for_unmodified_worker_json(online, pg):
    main, client, headers = online
    from backend.app.core import Worker
    with Session(pg[0]) as configurator:
        configurator.scalar(select(Worker).where(Worker.id == 'w').with_for_update())
        response = client.post('/api/v1/worker/heartbeat', headers=headers,
                               json={'slot_id': 's', 'execution_id': 'e', 'started': True})
        assert response.status_code == 200, response.text


@pytest.mark.parametrize('reason', ['scheduler_lock', 'execution_lock', 'execution_recheck'])
def test_control_busy_http_reason_is_not_lost(online, monkeypatch, reason):
    from backend.app.scheduling import ControlBusy
    main, client, headers = online
    # Exercise the real main exception handler, not a test-only FastAPI app.
    def busy(*args, **kwargs):
        raise ControlBusy(reason)
    from backend.app import recovery_launch
    monkeypatch.setattr(recovery_launch, 'prepare', busy)
    response = client.post('/api/v1/worker/executions/e/recovery/prepare', headers=headers, json={
        'generation':'test', 'request_id':'00000000-0000-0000-0000-000000000001',
        'launch_id':'00000000-0000-0000-0000-000000000002', 'boot_id':'boot', 'kind':'host'})
    assert response.status_code == 503, response.text
    assert response.json()['reason'] == reason
    assert response.headers['Retry-After'] == '1'


def test_start_confirmation_supersedes_stale_startup_timeout(online, pg):
    main, client, headers = online
    at = now()
    with main.SessionLocal() as s:
        run = s.get(Run, 'r')
        run.status = s.get(Job, 'j').status = 'STARTING'
        run.enqueued_at = at-timedelta(seconds=121)
        s.commit()
    with Session(pg[0]) as scheduler:
        stale = scheduler.get(Run, 'r')
        lock(scheduler)
        reply = client.post('/api/v1/worker/heartbeat', headers=headers,
                            json={'slot_id':'s', 'execution_id':'e', 'started':True})
        assert reply.status_code == 200
        reconcile(scheduler, at)
        scheduler.commit()
        assert stale.status == 'RUNNING'
        assert not stale.cleanup_confirmed
