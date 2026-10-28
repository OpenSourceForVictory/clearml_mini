import importlib
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_readiness_requires_only_database(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "ready.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    client = TestClient(main.app)
    assert client.get('/healthz').json()['status'] == 'ok'
    assert client.get('/readyz').json() == {'status': 'ready', 'database': 'ok'}
