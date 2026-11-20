"""Real PostgreSQL upgrade, rollback and batch cleanup of optional browse roots."""
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from backend.tests.test_execution_logs_postgres import pg, seed
from backend.app.core import ExecutionBrowseRoot, Run, Job, User
from backend.app.batch_deletion import remove


def test_result_directory_migration_and_deletion(pg,monkeypatch):
    engine,url=pg;monkeypatch.setenv('DATABASE_URL',url);cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'b841e23');factory=seed(engine,create=False)
    command.upgrade(cfg,'head')
    with factory() as s:
        s.add(ExecutionBrowseRoot(run_id='r',execution_id='e',snapshot={'node_name':'w','output_root':'/shared/custom'}));s.commit()
        s.get(Run,'r').status='FAILED';s.get(Run,'r').cleanup_confirmed=True;s.get(Job,'j').status='FAILED';s.commit()
        assert remove(s,'b',s.get(User,'u'))['deleted'];s.commit()
        assert s.get(ExecutionBrowseRoot,'r') is None
    command.downgrade(cfg,'b841e23')
    with engine.connect() as c:
        assert c.execute(text("select to_regclass('execution_browse_roots')")).scalar() is None
    command.upgrade(cfg,'head')


def test_first_mapping_is_atomic_across_readers(pg,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend.app import result_directories
    factory=seed(pg[0]);barrier=threading.Barrier(2)
    with factory() as s:
        s.add(ExecutionBrowseRoot(run_id='r',execution_id='e',snapshot={'node_name':'w','output_root':'/shared/custom'}));s.commit()
    calls=[]
    def bind(value):
        calls.append(1)
        return {**value,'storage_id':'first','marker':'stable','relative':'custom'}
    monkeypatch.setattr(result_directories,'bind',bind)
    def read():
        with factory() as s:
            run=s.get(Run,'r');barrier.wait(timeout=5)
            value=result_directories.location(s,run);s.commit();return value
    with ThreadPoolExecutor(2) as pool:
        a,b=pool.submit(read),pool.submit(read)
        assert a.result(timeout=10)==b.result(timeout=10)
    assert len(calls)==1
