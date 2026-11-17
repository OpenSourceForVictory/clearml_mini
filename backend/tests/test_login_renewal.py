import importlib
from datetime import datetime, timezone
import jwt
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "login.db"}')
    monkeypatch.setenv('JWT_SECRET', 'renewal-test-secret')
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base, User
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    with main.SessionLocal() as db:
        user = User(username='alice', password_hash=main.ph.hash('password'))
        db.add(user); db.commit(); identity=user.id
    return main, TestClient(main.app), identity


def headers(token):
    return {'Authorization': 'Bearer '+token}


def test_login_and_renew_preserve_origin(session):
    main, client, identity=session
    response=client.post('/api/v1/auth/login', json={'username':'alice','password':'password'})
    token=response.json()['access_token']; claims=jwt.decode(token, main.SECRET, algorithms=['HS256'])
    assert claims['exp']-claims['session_started_at']==1800
    for _ in range(2):
        response=client.post('/api/v1/auth/renew', headers=headers(token))
        assert response.status_code==200
        token=response.json()['access_token']; renewed=jwt.decode(token, main.SECRET, algorithms=['HS256'])
        assert renewed['sub']==identity and renewed['session_started_at']==claims['session_started_at']
        assert renewed['exp']<=claims['session_started_at']+43200


def test_renew_caps_absolute_lifetime(session):
    main, client, identity=session
    timestamp=int(datetime.now(timezone.utc).timestamp())
    token=jwt.encode({'sub':identity,'session_started_at':timestamp-43100,'exp':timestamp+60},main.SECRET,algorithm='HS256')
    response=client.post('/api/v1/auth/renew', headers=headers(token))
    assert response.status_code==200
    assert jwt.decode(response.json()['access_token'],main.SECRET,algorithms=['HS256'])['exp']==timestamp+100


@pytest.mark.parametrize('case',['expired','missing_origin','missing_exp','future','absolute','string','bool','tampered','missing_user','disabled','absent'])
def test_reject_invalid_renewal(session, case):
    main, client, identity=session
    timestamp=int(datetime.now(timezone.utc).timestamp())
    claims={'sub':identity,'session_started_at':timestamp,'exp':timestamp+1800}
    if case=='expired':claims['exp']=timestamp-1
    if case=='missing_origin':del claims['session_started_at']
    if case=='missing_exp':del claims['exp']
    if case=='future':claims['session_started_at']=timestamp+60
    if case=='absolute':claims['session_started_at']=timestamp-43200
    if case=='string':claims['session_started_at']=str(timestamp)
    if case=='bool':claims['session_started_at']=True
    if case=='missing_user':claims['sub']='missing'
    if case=='disabled':
        from backend.app.core import User
        with main.SessionLocal() as db:
            db.get(User,identity).active=False;db.commit()
    token=jwt.encode(claims,'wrong-key' if case=='tampered' else main.SECRET,algorithm='HS256')
    assert client.post('/api/v1/auth/renew',headers={} if case=='absent' else headers(token)).status_code==401
