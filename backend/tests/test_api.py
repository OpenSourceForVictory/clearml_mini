import importlib
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from argon2 import PasswordHasher

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_submit_idempotency_and_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "test.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base, Pool, User
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    with main.SessionLocal() as s:
        owner = User(username='owner', password_hash=PasswordHasher().hash('a-long-password'))
        other = User(username='other', password_hash=PasswordHasher().hash('another-password'))
        pool = Pool(name='mock', gpu_model='virtual')
        s.add_all([owner, other, pool])
        s.commit()
        pool_id = pool.id
    client = TestClient(main.app)
    def token(name, password):
        result = client.post('/api/v1/auth/login', json={'username': name, 'password': password})
        assert result.status_code == 200
        return {'Authorization': f'Bearer {result.json()["access_token"]}'}
    auth = token('owner', 'a-long-password')
    body = {'title': 'demo', 'benchmark': 'mock', 'model_ref': 'm', 'episodes': 3, 'pool_id': pool_id}
    headers = {**auth, 'Idempotency-Key': 'same'}
    first = client.post('/api/v1/jobs', json=body, headers=headers)
    second = client.post('/api/v1/jobs', json=body, headers=headers)
    assert first.status_code == 201
    assert client.get(f'/api/v1/jobs/{first.json()["id"]}', headers=auth).json()['owner_name'] == 'owner'
    assert first.json()['id'] == second.json()['id']
    other_auth = token('other', 'another-password')
    assert client.get(f'/api/v1/jobs/{first.json()["id"]}', headers=other_auth).status_code == 404
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/cancel', headers=other_auth).status_code == 404
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/cancel', headers=auth).json()['status'] == 'CANCELLED'
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/retry', headers=auth).json()['attempt'] == 2


def test_auth_pool_worker_and_admin_boundaries(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "contracts.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base, Pool, Slot, User, now
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    with main.SessionLocal() as s:
        admin = User(username='admin', password_hash=PasswordHasher().hash('admin-password'), role='admin')
        alice = User(username='alice', password_hash=PasswordHasher().hash('alice-password'))
        bob = User(username='bob', password_hash=PasswordHasher().hash('bob-password'))
        pool = Pool(name='a100', gpu_model='A100', min_vram_gb=40)
        s.add_all([admin, alice, bob, pool])
        s.flush()
        s.add(Slot(node_name='private-host', gpu_index=0, gpu_uuid='GPU-private', pool_id=pool.id,
                   queue_name='private-queue', last_seen_at=now()))
        s.commit()
        pool_id = pool.id
    client = TestClient(main.app)
    assert client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'wrong'}).status_code == 401
    assert client.get('/api/v1/pools').status_code == 401

    def auth(name, password):
        response = client.post('/api/v1/auth/login', json={'username': name, 'password': password})
        assert response.status_code == 200
        return {'Authorization': f'Bearer {response.json()["access_token"]}'}

    alice_auth = auth('alice', 'alice-password')
    bob_auth = auth('bob', 'bob-password')
    admin_auth = auth('admin', 'admin-password')
    assert client.get('/api/v1/auth/me', headers=alice_auth).json()['username'] == 'alice'
    assert client.get('/api/v1/pools', headers=alice_auth).json()[0]['name'] == 'a100'
    assert client.get('/api/v1/workers', headers=alice_auth).json() == {'total': 1, 'online': 1}
    assert client.get('/api/v1/workers', headers=admin_auth).json()[0]['node_name'] == 'private-host'
    body = {'title': 'test', 'benchmark': 'mock', 'model_ref': 'model', 'episodes': 1, 'pool_id': pool_id}
    assert client.post('/api/v1/jobs', json={**body, 'episodes': 0}, headers=alice_auth).status_code == 400
    assert client.post('/api/v1/jobs', json={**body, 'benchmark': 'robotwin'}, headers=alice_auth).status_code == 400
    alice_job = client.post('/api/v1/jobs', json=body, headers={**alice_auth, 'Idempotency-Key': 'same'}).json()['id']
    bob_job = client.post('/api/v1/jobs', json=body, headers={**bob_auth, 'Idempotency-Key': 'same'}).json()['id']
    assert alice_job != bob_job
    assert [x['id'] for x in client.get('/api/v1/jobs', headers=alice_auth).json()] == [alice_job]
    assert client.get('/api/v1/jobs?page=0', headers=alice_auth).status_code == 400
    assert client.get('/api/v1/jobs?page_size=101', headers=alice_auth).status_code == 400
    assert client.get('/api/v1/jobs?status=NOT_A_STATE', headers=alice_auth).status_code == 400
    assert len(client.get('/api/v1/jobs?status=PENDING', headers=alice_auth).json()) == 1
    assert client.get(f'/api/v1/jobs/{bob_job}', headers=alice_auth).status_code == 404
    assert client.get('/api/v1/admin/jobs', headers=alice_auth).status_code == 403
    assert len(client.get('/api/v1/admin/jobs', headers=admin_auth).json()) == 2

    # Environment checks are opt-in and cannot masquerade as checkpoint evaluations.
    render = {**body, 'benchmark': 'robotwin-render', 'model_ref': 'sapien-render'}
    monkeypatch.delenv('ROBOTWIN_RENDER_ENABLED', raising=False)
    assert client.post('/api/v1/jobs', json=render, headers=alice_auth).status_code == 400
    assert client.get('/api/v1/benchmarks', headers=alice_auth).json() == [{'id': 'mock', 'name': '模拟评测'}, {'id': 'cs-client', 'name': 'CS 客户端评测'}]
    monkeypatch.setenv('ROBOTWIN_RENDER_ENABLED', '1')
    assert client.post('/api/v1/jobs', json={**render, 'model_ref': 'checkpoint'}, headers=alice_auth).status_code == 400
    assert client.post('/api/v1/jobs', json={**render, 'episodes': 101}, headers=alice_auth).status_code == 400
    assert client.post('/api/v1/jobs', json=render, headers=alice_auth).status_code == 201
    with main.SessionLocal() as s:
        s.get(Pool, pool_id).gpu_model = 'virtual'
        s.commit()
    assert client.post('/api/v1/jobs', json=render, headers=alice_auth).status_code == 400


def test_pending_limit_and_disabled_pool(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "limits.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    monkeypatch.setenv('MAX_PENDING_PER_USER', '2')
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base, Pool, User
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    with main.SessionLocal() as s:
        s.add(User(username='alice', password_hash=PasswordHasher().hash('password')))
        enabled = Pool(name='enabled', gpu_model='mock')
        disabled = Pool(name='disabled', gpu_model='mock', enabled=False)
        s.add_all([enabled, disabled])
        s.commit()
        enabled_id, disabled_id = enabled.id, disabled.id
    client = TestClient(main.app)
    token = client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'password'}).json()['access_token']
    auth = {'Authorization': f'Bearer {token}'}
    body = {'title': 'test', 'benchmark': 'mock', 'model_ref': 'm', 'episodes': 1, 'pool_id': enabled_id}
    assert client.post('/api/v1/jobs', json={**body, 'pool_id': disabled_id}, headers=auth).status_code == 400
    assert client.post('/api/v1/jobs', json=body, headers={**auth, 'Idempotency-Key': ' '}).status_code == 400
    first = client.post('/api/v1/jobs', json=body, headers={**auth, 'Idempotency-Key': 'one'})
    assert first.status_code == 201
    assert client.post('/api/v1/jobs', json=body, headers={**auth, 'Idempotency-Key': 'two'}).status_code == 201
    assert client.post('/api/v1/jobs', json=body, headers={**auth, 'Idempotency-Key': 'one'}).json()['id'] == first.json()['id']
    assert client.post('/api/v1/jobs', json=body, headers=auth).status_code == 429
