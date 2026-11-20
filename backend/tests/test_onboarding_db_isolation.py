"""Slow onboarding IPC must not reserve database connections or scheduling locks."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from backend.app.core import Run
from backend.tests.test_execution import env  # noqa: F401
from backend.tests.test_execution_logs_postgres import pg  # noqa: F401
from backend.tests.test_shared_artifact_lifecycle import shared_pg  # noqa: F401


def test_two_slow_executor_lists_leave_small_pool_and_heartbeat_available(shared_pg, monkeypatch):
    from backend.app import ssh_onboarding
    value, engine = shared_pg
    main, client, auth, token, job, task, *_ = value
    with main.SessionLocal() as session:
        slot = session.get(Run, task['run_id']).slot_id
    entered = threading.Event()
    release = threading.Event()
    mutex = threading.Lock()
    count = 0

    def slow(path, body=None):
        nonlocal count
        with mutex:
            count += 1
            if count == 2:
                entered.set()
        assert release.wait(10)
        raise HTTPException(503, 'isolated executor timeout')

    monkeypatch.setattr(ssh_onboarding, 'executor', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(client.get, '/api/v1/admin/ssh-onboarding', headers=auth['admin']) for _ in range(2)]
        try:
            assert entered.wait(5)
            assert engine.pool.checkedout() == 0
            response = client.post('/api/v1/worker/heartbeat', headers=token,
                                   json={'slot_id': slot, 'execution_id': task['execution_id']})
            assert response.status_code == 200, response.text
            assert client.post('/api/v1/jobs/' + job + '/cancel', headers=auth['alice']).status_code == 202
        finally:
            release.set()
        assert all(request.result(timeout=5).status_code == 503 for request in requests)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize('operation', ['check', 'confirm', 'verify', 'cancel', 'trust', 'cleanup-check', 'cleanup-complete'])
def test_onboarding_ipc_releases_connection_and_cleanup_lock(shared_pg, monkeypatch, operation):
    from backend.app import ssh_onboarding
    from backend.app.core import Worker
    from backend.app.scheduling import LOCK_ID
    from backend.tests.test_execution import node
    from backend.tests.test_ssh_onboarding import form

    value, engine = shared_pg
    main, client, auth, *_ = value
    path = '/api/v1/admin/ssh-onboarding/' + ('check' if operation == 'check' else 'op/' + operation)
    body = form() if operation == 'check' else {}
    identity = None
    if operation.startswith('cleanup-'):
        node((main, client, auth), name='cleanup-target')
        item = next(row for row in client.get('/api/v1/admin/nodes', headers=auth['admin']).json()
                    if row['node_name'] == 'cleanup-target')
        identity = item['id']
        prefix = '/api/v1/admin/nodes/' + identity
        assert client.post(prefix + '/unmanage', headers=auth['admin'], json={'expected_revision': 0}).status_code == 200
        path = prefix + '/cleanup/' + operation.removeprefix('cleanup-')
        body = ({**form(), 'username': 'root', 'expected_revision': 1} if operation == 'cleanup-check'
                else {'operation_id': 'a' * 32, 'state': 'SUCCEEDED'})
    calls = []

    def external(endpoint, payload=None):
        calls.append(endpoint)
        assert engine.pool.checkedout() == 0
        with engine.begin() as connection:
            assert connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_ID}).scalar()
        if operation == 'cleanup-check':
            # Configuration may change during SSH. A later begin must recheck
            # the revision rather than treating this preliminary check as a grant.
            with main.SessionLocal() as session:
                session.get(Worker, identity).management_revision += 1
                session.commit()
        return {'operations': []}

    monkeypatch.setattr(ssh_onboarding, 'executor', external)
    response = client.post(path, headers=auth['admin'], json=body)
    assert response.status_code == (409 if operation == 'cleanup-complete' else 200), response.text
    assert len(calls) == 1
    if operation == 'cleanup-check':
        response = client.post(prefix + '/cleanup/begin', headers=auth['admin'],
                               json={'expected_revision': 1, 'operation_id': 'b' * 32})
        assert response.status_code == 409, response.text
    assert engine.pool.checkedout() == 0
