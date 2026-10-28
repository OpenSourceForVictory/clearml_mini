import importlib
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_readiness_reports_clearml_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "ready.db"}')
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    monkeypatch.delenv('CLEARML_API_HOST', raising=False)
    from backend.app import main
    importlib.reload(main)
    from backend.app.core import Base
    Base.metadata.create_all(main.SessionLocal.kw['bind'])
    client = TestClient(main.app)
    assert client.get('/healthz').json()['status'] == 'ok'
    degraded = client.get('/readyz')
    assert degraded.status_code == 503
    assert degraded.json()['clearml'] == 'not_configured'
    monkeypatch.setenv('CLEARML_API_HOST', 'http://clearml.example:8008')

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return None

    def ping(request, timeout):
        assert request.full_url == 'http://clearml.example:8008/debug.ping'
        assert request.get_method() == 'POST'
        assert timeout == 2
        return Response()

    monkeypatch.setattr(main.urllib.request, 'urlopen', ping)
    assert client.get('/readyz').json()['status'] == 'ready'
