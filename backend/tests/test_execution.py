import importlib
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.core import Base, Job, Pool, Run, Slot, User, now
from backend.app.scheduling import tick


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "worker.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    from backend.app import main
    importlib.reload(main)
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    with main.SessionLocal() as s:
        s.add(User(id='admin', username='admin', role='admin', password_hash=main.ph.hash('admin-password')))
        s.add(User(id='alice', username='alice', password_hash=main.ph.hash('alice-password')))
        s.add(User(id='bob', username='bob', password_hash=main.ph.hash('bob-password')))
        s.add(Pool(id='mock', name='mock', gpu_model='virtual'))
        s.commit()
    client = TestClient(main.app)
    auth = {}
    for name in ('admin', 'alice', 'bob'):
        t = client.post('/api/v1/auth/login', json={'username': name, 'password': name+'-password'}).json()['access_token']
        auth[name] = {'Authorization': 'Bearer ' + t}
    return main, client, auth


def node(env, name='node', count=1):
    main, c, auth = env
    response = c.post('/api/v1/admin/worker-credentials', headers=auth['admin'], json={'node_name': name, 'max_concurrent': count})
    assert response.status_code == 201, response.text
    token = {'Authorization': 'Bearer ' + response.json()['token']}
    slots = []
    for i in range(count):
        r = c.post('/api/v1/worker/register', headers=token, json={'pool_id': 'mock', 'gpu_index': i, 'gpu_uuid': f'mock:{name}:{i}', 'gpu_model': 'virtual', 'vram_gb': 0})
        assert r.status_code == 200, r.text
        slots.append(r.json()['slot_id'])
    return token, slots


def submit(env, owner='alice'):
    _, c, auth = env
    r = c.post('/api/v1/jobs', headers=auth[owner], json={'title': 'demo', 'benchmark': 'mock', 'model_ref': 'checkpoint', 'episodes': 3, 'pool_id': 'mock'})
    assert r.status_code == 201
    return r.json()['id']


def claim(env, token, slot):
    main, c, _ = env
    tick(main.SessionLocal)
    r = c.post(f'/api/v1/worker/slots/{slot}/claim', headers=token)
    assert r.status_code == 200, r.text
    return r.json()['task']


def test_idle_claim_does_not_take_scheduler_lock(env, monkeypatch):
    from backend.app import execution
    _, client, _ = env
    token, slots = node(env)
    monkeypatch.setenv('DB_BACKPRESSURE_ENABLED', '1')
    monkeypatch.setattr(execution, 'lock', lambda *args, **kwargs: pytest.fail('idle claim took scheduler lock'))
    response = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token)
    assert response.status_code == 200
    assert response.json() == {'task': None, 'cleanup': []}


def test_claim_lock_busy_defers_without_losing_reservation(env, monkeypatch):
    from backend.app import execution
    main, client, _ = env
    token, slots = node(env)
    job_id = submit(env)
    assert tick(main.SessionLocal) == 1
    monkeypatch.setenv('DB_BACKPRESSURE_ENABLED', '1')
    monkeypatch.setattr(execution, 'lock', lambda session, wait=False: False)
    path = f'/api/v1/worker/slots/{slots[0]}/claim'
    deferred = client.post(path, headers=token)
    assert deferred.status_code == 200
    assert deferred.json() == {'task': None, 'cleanup': []}
    with main.SessionLocal() as session:
        run = session.scalar(select(Run).where(Run.job_id == job_id))
        assert run.status == 'RESERVED'
    monkeypatch.setattr(execution, 'lock', lambda session, wait=False: True)
    task = client.post(path, headers=token).json()['task']
    assert task['job_id'] == job_id
    assert client.post(path, headers=token).json()['task']['execution_id'] == task['execution_id']
    assert client.post(path, headers=token,
                       json={'known_execution_ids': [task['execution_id']]}).json() == {'task': None, 'cleanup': []}


def test_claim_rechecks_candidate_after_lock(env, monkeypatch):
    from backend.app import execution
    main, client, _ = env
    token, slots = node(env)
    job_id = submit(env)
    assert tick(main.SessionLocal) == 1
    monkeypatch.setenv('DB_BACKPRESSURE_ENABLED', '1')

    def reservation_was_cancelled(session, wait=False):
        run = session.scalar(select(Run).where(Run.job_id == job_id))
        run.status = 'CANCELLED'
        run.cleanup_confirmed = True
        session.flush()
        return True

    monkeypatch.setattr(execution, 'lock', reservation_was_cancelled)
    response = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token)
    assert response.status_code == 200
    assert response.json() == {'task': None, 'cleanup': []}


def test_job_lookup_errors_and_admin_health_permissions(env):
    _, client, auth = env
    missing = '00000000-0000-0000-0000-000000000000'
    assert client.get(f'/api/v1/jobs/{missing}', headers=auth['alice']).status_code == 404
    assert client.post(f'/api/v1/jobs/{missing}/cancel', headers=auth['alice']).status_code == 404
    assert client.post(f'/api/v1/jobs/{missing}/retry', headers=auth['alice']).status_code == 404
    assert client.get(f'/api/v1/jobs/{missing}/logs', headers=auth['alice']).status_code == 404
    assert client.get('/api/v1/admin/health', headers=auth['alice']).status_code == 403
    health = client.get('/api/v1/admin/health', headers=auth['admin'])
    assert health.status_code == 200
    assert health.json()['quarantined_slots'] == 0
    job_id = submit(env)
    assert client.get(f'/api/v1/jobs/{job_id}/logs', headers=auth['alice'],
                      params={'run_id': missing}).status_code == 404


def test_worker_protocol_and_permissions(env):
    main, c, auth = env
    token, slots = node(env)
    assert c.post('/api/v1/worker/heartbeat', headers=auth['alice'], json={'slot_id': slots[0]}).status_code == 401
    token2, other = node(env, 'other')
    assert c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token2).status_code == 404
    job = submit(env)
    task = claim(env, token, slots[0])
    assert task['job_id'] == job
    assert claim(env, token, slots[0])['execution_id'] == task['execution_id']
    ex = task['execution_id']
    assert c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0], 'execution_id': ex}).json()['cancel'] is False
    for _ in range(2):
        assert c.post(f'/api/v1/worker/executions/{ex}/logs', headers=token, json={'sequence': 0, 'content': 'episode 1\n'}).status_code == 200
    assert c.post(f'/api/v1/worker/executions/{ex}/logs', headers=token, json={'sequence': 0, 'content': 'different'}).status_code == 409
    assert c.post(f'/api/v1/worker/executions/{ex}/complete', headers=token, json={'status': 'SUCCEEDED'}).status_code == 400
    result = {'status': 'SUCCEEDED', 'metrics': {'success_rate': 1, 'episode_count': 3, 'runtime': .1}}
    for _ in range(2):
        assert c.post(f'/api/v1/worker/executions/{ex}/complete', headers=token, json=result).status_code == 200
    detail = c.get(f'/api/v1/jobs/{job}', headers=auth['alice']).json()
    assert detail['status'] == 'SUCCEEDED'
    assert detail['attempts'][0]['metrics']['success_rate'] == 1
    assert c.get(f'/api/v1/jobs/{job}/logs', headers=auth['bob']).status_code == 404
    assert c.get(f'/api/v1/jobs/{job}/logs', headers=auth['alice']).json()['chunks'][0]['content'] == 'episode 1\n'
    assert c.post(f'/api/v1/worker/executions/{ex}/logs', headers=token, json={'sequence': 1, 'content': 'late'}).status_code == 409


def test_cancel_timeout_quarantine_and_retry(env):
    main, c, auth = env
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    ex = task['execution_id']
    c.post(f'/api/v1/jobs/{job}/cancel', headers=auth['alice'])
    assert c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0], 'execution_id': ex}).json()['cancel']
    assert c.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).status_code == 409
    assert c.post(f'/api/v1/worker/executions/{ex}/complete', headers=token, json={'status': 'CANCELLED'}).status_code == 200
    assert c.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).json()['attempt'] == 2
    new = claim(env, token, slots[0])
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.execution_id == new['execution_id']))
        run.lease_expires_at = now() - timedelta(seconds=120)
        s.commit()
    tick(main.SessionLocal)
    assert c.get(f'/api/v1/jobs/{job}', headers=auth['alice']).json()['status'] == 'TIMED_OUT'
    assert c.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).status_code == 409
    assert c.post(f'/api/v1/worker/executions/{new["execution_id"]}/complete', headers=token, json={'status': 'SUCCEEDED', 'metrics': {'success_rate': 1, 'episode_count': 3, 'runtime': 1}}).status_code == 409
    assert c.post(f'/api/v1/worker/executions/{new["execution_id"]}/cleanup', headers=token).status_code == 200
    assert c.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).status_code == 202
    assert claim(env, token, slots[0])['execution_id'] != new['execution_id']


def test_fairness_fifo_and_gpu_uuid(env, monkeypatch):
    main, c, auth = env
    token, slots = node(env, count=2)
    for owner in ('alice', 'alice', 'bob', 'bob'):
        submit(env, owner)
    assert tick(main.SessionLocal) == 2
    with main.SessionLocal() as s:
        owners = [s.get(Job, r.job_id).owner_id for r in s.scalars(select(Run).where(Run.status == 'RESERVED'))]
        assert sorted(owners) == ['alice', 'bob']
    assert tick(main.SessionLocal) == 0
    token2, _ = node(env, 'second')
    body = {'pool_id': 'mock', 'gpu_index': 4, 'gpu_uuid': 'mock:node:0', 'gpu_model': 'virtual', 'vram_gb': 0}
    assert c.post('/api/v1/worker/register', headers=token2, json=body).status_code == 409


def test_log_cap_and_stale_slot(env):
    main, c, _ = env
    token, slots = node(env)
    submit(env)
    task = claim(env, token, slots[0])
    ex = task['execution_id']
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.execution_id == ex))
        run.log_bytes = 262144
        s.commit()
    assert c.post(f'/api/v1/worker/executions/{ex}/logs', headers=token, json={'sequence': 0, 'content': 'overflow'}).status_code == 413
    assert c.post(f'/api/v1/worker/executions/{ex}/logs', headers=token, json={'sequence': 0, 'content': 'x'*8193}).status_code == 400


def test_admin_provisioning_and_token_revocation(env):
    main, c, auth = env
    assert c.post('/api/v1/admin/users', headers=auth['alice'], json={'username': 'evil', 'password': 'long-password'}).status_code == 403
    assert c.post('/api/v1/admin/users', headers=auth['admin'], json={'username': 'new', 'password': 'long-password'}).status_code == 201
    token, slots = node(env)
    nodes = c.get('/api/v1/admin/nodes', headers=auth['admin']).json()
    node_id = nodes[0]['id']
    rotated = c.post(f'/api/v1/admin/nodes/{node_id}/rotate-token', headers=auth['admin'])
    assert rotated.status_code == 200
    assert c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0]}).status_code == 401
    fresh = {'Authorization': 'Bearer '+rotated.json()['token']}
    assert c.post('/api/v1/worker/heartbeat', headers=fresh, json={'slot_id': slots[0]}).status_code == 200
    assert c.patch(f'/api/v1/admin/nodes/{node_id}', headers=auth['admin'], json={'enabled': False}).status_code == 200
    assert c.post('/api/v1/worker/heartbeat', headers=fresh, json={'slot_id': slots[0]}).status_code == 200  # disabled nodes still drain executions
