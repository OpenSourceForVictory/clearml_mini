# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
import threading
import json
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from backend.tests.test_execution_logs_postgres import pg, seed  # noqa: F401
from backend.app.core import SharedArtifactRoot, RunArtifact, Run, Job, User
from backend.app.shared_artifacts import save_root
from backend.app.batch_deletion import remove


def test_shared_migration_registration_and_delete(pg, monkeypatch):
    engine, url = pg
    monkeypatch.setenv('DATABASE_URL', url)
    cfg = Config('backend/alembic.ini')
    command.upgrade(cfg, 'z629c01')
    factory = seed(engine, create=False)
    with factory() as s:
        s.add(RunArtifact(id='old', run_id='r', name='old.txt', data=b'legacy', size_bytes=6, sha256='legacy'))
        s.commit()
    command.upgrade(cfg, 'head')
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([dict(id='share', node_names=['w'],
        node_root='/shared', api_root='/mnt/share', marker='test-shared-store-001')]))
    barrier = threading.Barrier(2)
    def register():
        with factory() as s:
            run = s.get(Run, 'r')
            barrier.wait(timeout=5)
            result = save_root(s, run, 'w', '/shared/runs/e/output')
            s.commit()
            return result
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(register), pool.submit(register)
        assert a.result(timeout=10) == b.result(timeout=10)
    with factory() as s:
        assert len(s.scalars(select(SharedArtifactRoot)).all()) == 1
        assert s.get(RunArtifact, 'old').data == b'legacy'
    # Downgrade only removes directory metadata; historical bytes survive.
    command.downgrade(cfg, 'z629c01')
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('shared_artifact_roots')")).scalar() is None
    command.upgrade(cfg, 'head')
    with factory() as s:
        assert s.get(RunArtifact, 'old').data == b'legacy'
        save_root(s, s.get(Run,'r'), 'w', '/shared/runs/e/output')
        run = s.get(Run,'r'); run.status='FAILED'; run.cleanup_confirmed=True
        s.get(Job,'j').status='FAILED'; s.commit()
        assert remove(s,'b',s.get(User,'u'))['deleted']; s.commit()
        assert not s.scalars(select(SharedArtifactRoot)).all()
        assert not s.scalars(select(RunArtifact)).all()
