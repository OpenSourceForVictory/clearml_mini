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
    assert first.json()['id'] == second.json()['id']
    other_auth = token('other', 'another-password')
    assert client.get(f'/api/v1/jobs/{first.json()["id"]}', headers=other_auth).status_code == 404
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/cancel', headers=other_auth).status_code == 404
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/cancel', headers=auth).json()['status'] == 'CANCELLED'
    assert client.post(f'/api/v1/jobs/{first.json()["id"]}/retry', headers=auth).json()['attempt'] == 2
