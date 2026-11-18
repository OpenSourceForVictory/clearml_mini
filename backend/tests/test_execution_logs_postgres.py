"""Opt-in against disposable PostgreSQL only; never touches platform databases."""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url
from backend.app.core import Base, User, Pool, Worker, Slot, EvaluationBatch, Job, Run, RunLogState, RunLogChunk, now
from backend.app.execution_logs import append, LogChunk
from backend.app.batch_deletion import remove


@pytest.fixture
def pg(monkeypatch):
    if not os.getenv('LOG_TEST_DATABASE_URL'):
        pytest.skip('requires disposable PostgreSQL server')
    url = make_url(os.environ['LOG_TEST_DATABASE_URL'])
    name = 'log_test_'+uuid.uuid4().hex
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE '+name))
    engine = create_engine(url.set(database=name), connect_args={'options': '-c lock_timeout=3000 -c statement_timeout=5000'})
    try:
        yield engine, url.set(database=name).render_as_string(hide_password=False)
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text('DROP DATABASE '+name+' WITH (FORCE)'))
        admin.dispose()


def seed(engine, create=True):
    if create: Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add(User(id='u', username='u', role='admin', password_hash='unused'))
        s.add(Pool(id='p', name='p', gpu_model='virtual'))
        s.add(Worker(id='w', node_name='w', token_hash='unused'))
        s.commit()
        s.add(Slot(id='s', node_name='w', queue_name='log-test', worker_id='w', pool_id='p', gpu_index=0, gpu_uuid='virtual', gpu_model='virtual'))
        s.add(EvaluationBatch(id='b', owner_id='u', title='logs'))
        s.commit()
        s.add(Job(id='j', batch_id='b', owner_id='u', pool_id='p', title='log-test', benchmark='mock', model_ref='m', episodes=1, status='RUNNING'))
        s.commit()
        s.add(Run(id='r', job_id='j', attempt=1, slot_id='s', execution_id='e', status='RUNNING', cleanup_confirmed=False, lease_expires_at=now()+timedelta(minutes=2)))
        s.commit()
    return factory


def test_concurrent_replay_and_delete(pg):
    from fastapi import HTTPException
    factory = seed(pg[0])
    barrier = threading.Barrier(2)
    def upload():
        with factory() as s:
            w = s.get(Worker, 'w')
            barrier.wait(timeout=5)
            result = append(s, 'e', w, LogChunk(sequence=0, content='hello'))
            s.commit()
            return result
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(upload) for _ in range(2)]
        assert all(f.result(timeout=10)['accepted'] for f in futures)
    with factory() as s:
        assert len(s.scalars(select(RunLogChunk)).all()) == 1
        run = s.get(Run, 'r'); run.status='FAILED'; run.cleanup_confirmed=True
        s.get(Job, 'j').status='FAILED'; s.commit()
    barrier = threading.Barrier(2)
    def late_upload():
        with factory() as s:
            w = s.get(Worker, 'w'); barrier.wait(timeout=5)
            try: append(s, 'e', w, LogChunk(sequence=1, content='late'))
            except HTTPException as exc: return exc.status_code
            raise AssertionError('late logs accepted')
    def delete_batch():
        with factory() as s:
            u=s.get(User,'u');barrier.wait(timeout=5)
            result=remove(s,'b',u);s.commit();return result
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(late_upload);b=pool.submit(delete_batch)
        assert a.result(timeout=10) in (404,409)
        assert b.result(timeout=10)['deleted']
    with factory() as s:
        assert not s.scalars(select(RunLogChunk)).all()
        assert not s.scalars(select(RunLogState)).all()


def test_migration_preserves_legacy(pg, monkeypatch):
    from alembic import command
    from alembic.config import Config
    engine, url = pg
    monkeypatch.setenv('DATABASE_URL', url)
    cfg = Config('backend/alembic.ini')
    command.upgrade(cfg, 'x407a89')
    factory = seed(engine, create=False)
    from backend.app.core import RunLog
    with factory() as s:
        s.add(RunLog(run_id='r', sequence=0, content='legacy log preserved'));s.commit()
    with engine.begin() as c:
        # Schema sentinel validates both directions without inventing invalid business rows.
        before = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='run_logs' ORDER BY ordinal_position")).scalars().all()
    command.upgrade(cfg, 'head')
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM run_log_states")).scalar()==0
        assert c.execute(text("SELECT content FROM run_logs WHERE run_id='r'")).scalar()=='legacy log preserved'
        assert c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='run_logs' ORDER BY ordinal_position")).scalars().all()==before
    command.downgrade(cfg, 'x407a89')
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('run_log_states')")).scalar() is None
        assert c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='run_logs' ORDER BY ordinal_position")).scalars().all()==before


def test_upload_races_completion(pg):
    from fastapi import HTTPException
    factory=seed(pg[0]);barrier=threading.Barrier(2)
    def upload():
        with factory() as s:
            w=s.get(Worker,'w');barrier.wait(timeout=5)
            try:
                append(s,'e',w,LogChunk(sequence=0,content='last'));s.commit();return 200
            except HTTPException as exc:return exc.status_code
    def complete():
        with factory() as s:
            barrier.wait(timeout=5)
            run=s.scalar(select(Run).where(Run.id=='r').with_for_update())
            run.status='FAILED';run.cleanup_confirmed=True;s.commit()
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(upload);b=pool.submit(complete)
        assert a.result(timeout=10) in (200,409);b.result(timeout=10)
    with factory() as s:
        assert s.get(Run,'r').status=='FAILED'
        with pytest.raises(HTTPException) as error:
            append(s,'e',s.get(Worker,'w'),LogChunk(sequence=1,content='late'))
        assert error.value.status_code==409


def test_api_upload_cost_and_no_global_lock(pg, monkeypatch, tmp_path):
    import json,time
    from sqlalchemy import event
    from fastapi.testclient import TestClient
    monkeypatch.setenv('JWT_SECRET','local-log-test-only')
    monkeypatch.setenv('DATABASE_URL',pg[1])
    from backend.app import main
    from backend.app.execution import token_hash
    from backend.app.scheduling import lock
    factory=seed(pg[0]);monkeypatch.setattr(main,'SessionLocal',factory)
    with factory() as s:
        s.get(Worker,'w').token_hash=token_hash('wk_local_log_test');s.commit()
    client=TestClient(main.app);headers={'Authorization':'Bearer wk_local_log_test'}
    statements=[]
    def record(conn,cursor,sql,params,context,many):statements.append(sql)
    event.listen(pg[0],'before_cursor_execute',record)
    metrics={}
    try:
        for version in (1,2):
            statements.clear();started=time.monotonic()
            count=8 if version==1 else 1
            for i in range(count):
                r=client.post('/api/v1/worker/executions/e/'+('logs' if version==1 else 'logs-v2'),
                              headers=headers,json={'sequence':i,'content':'x'*(2048 if version==1 else 16384)})
                assert r.status_code==200,r.text
            metrics[str(version)]={'requests':count,'sql_statements':len(statements),
                                  'global_lock_calls':sum('pg_advisory' in sql for sql in statements),
                                  'elapsed_ms':round(1000*(time.monotonic()-started),2)}
        assert metrics['2']['sql_statements'] < metrics['1']['sql_statements']
        assert metrics['2']['global_lock_calls']==0
        with factory() as blocker:
            lock(blocker)
            # Different session owns the global lock, yet v2 can still append.
            response=client.post('/api/v1/worker/executions/e/logs-v2',headers=headers,
                                 json={'sequence':1,'content':'while scheduler locked'})
            assert response.status_code==200
        destination=os.getenv('LOG_BENCHMARK_OUTPUT')
        if destination:
            from pathlib import Path
            Path(destination).write_text(json.dumps(metrics,indent=2))
    finally:event.remove(pg[0],'before_cursor_execute',record)
