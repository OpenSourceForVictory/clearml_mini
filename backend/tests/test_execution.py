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
