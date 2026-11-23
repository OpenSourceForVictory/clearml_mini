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


def test_start_transition_still_uses_global_lock(online):
    main, client, headers = online
    with main.SessionLocal() as s:
        s.get(Run, 'r').status = 'STARTING'
        s.commit()
    # Use a separate engine so the reserved HTTP pool is not exhausted.
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    engine = create_engine(main.SessionLocal.kw['bind'].url)
    try:
        with sessionmaker(engine)() as holder:
            lock(holder)
            response = client.post('/api/v1/worker/heartbeat', headers=headers,
                                   json={'slot_id': 's', 'execution_id': 'e', 'started': True})
            assert response.status_code == 503
    finally:
        engine.dispose()


def test_reconcile_skips_locked_run_and_reclaims_after_release(pg):
    factory = seed(pg[0])
    at = now()
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = at - timedelta(seconds=31)
        s.commit()
    with factory() as uploader, factory() as scheduler:
        uploader.scalar(select(Run).where(Run.id == 'r').with_for_update())
        lock(scheduler)
        reconcile(scheduler, at)
        scheduler.commit()
        assert scheduler.get(Run, 'r').status == 'RUNNING'
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
