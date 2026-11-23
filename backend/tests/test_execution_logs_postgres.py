"""Opt-in against disposable PostgreSQL only; never touches platform databases."""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import make_url
from backend.app.core import Base, User, Pool, Worker, Slot, Job, Run, RunLogState, RunLogChunk, now
from backend.app.execution_logs import append, LogChunk, terminal_log_lock
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
        # Migration tests seed schemas older than the current ORM, so insert
        # only columns that existed since evaluation_batches was introduced.
        s.execute(text("INSERT INTO evaluation_batches (id, owner_id, title, created_at) VALUES ('b', 'u', 'logs', :created_at)"),
                  {'created_at': now()})
        s.commit()
        s.add(Job(id='j', batch_id='b', owner_id='u', pool_id='p', title='log-test', benchmark='mock', model_ref='m', episodes=1, status='RUNNING'))
        s.commit()
        s.add(Run(id='r', job_id='j', attempt=1, slot_id='s', execution_id='e', status='RUNNING', cleanup_confirmed=False, lease_expires_at=now()+timedelta(minutes=2)))
        s.commit()
    return factory


@pytest.fixture
def api(pg, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('JWT_SECRET', 'local-log-test-only')
    from backend.app import main
    from backend.app.execution import token_hash
    factory = seed(pg[0])
    monkeypatch.setattr(main, 'SessionLocal', factory)
    with factory() as s:
        s.get(Worker, 'w').token_hash = token_hash('wk_local_log_test')
        s.commit()
    with TestClient(main.app) as client:
        yield client, {'Authorization': 'Bearer wk_local_log_test'}, factory


def test_log_upload_does_not_wait_for_heartbeat_run_lock(api):
    client, headers, factory = api
    with factory() as heartbeat:
        heartbeat.scalar(select(Run).where(Run.id == 'r').with_for_update(key_share=True))
        response = client.post('/api/v1/worker/executions/e/logs-v2', headers=headers,
                               json={'sequence': 0, 'content': 'during renewal'})
        assert response.status_code == 200, response.text


def test_heartbeat_does_not_wait_for_log_cursor_lock(api):
    client, headers, factory = api
    path = '/api/v1/worker/executions/e/logs-v2'
    assert client.post(path, headers=headers, json={'sequence': 0, 'content': 'first'}).status_code == 200
    with factory() as logger, ThreadPoolExecutor(1) as pool:
        logger.scalar(select(RunLogState).where(RunLogState.run_id == 'r').with_for_update())
        pending = pool.submit(client.post, path, headers=headers,
                              json={'sequence': 1, 'content': 'second'})
        time.sleep(.05)
        assert not pending.done()
        heartbeat = client.post('/api/v1/worker/heartbeat', headers=headers,
                                json={'slot_id': 's', 'execution_id': 'e', 'started': True})
        assert heartbeat.status_code == 200, heartbeat.text
        logger.commit()
        assert pending.result(timeout=5).status_code == 200


def test_completion_waits_for_inflight_log_then_rejects_late_chunk(api):
    client, headers, factory = api
    path = '/api/v1/worker/executions/e/logs-v2'
    with factory() as logger, ThreadPoolExecutor(1) as pool:
        terminal_log_lock(logger, 'r')
        pending = pool.submit(client.post, '/api/v1/worker/executions/e/complete',
                              headers=headers, json={'status': 'FAILED'})
        time.sleep(.05)
        assert not pending.done()
        assert append(logger, 'e', logger.get(Worker, 'w'),
                      LogChunk(sequence=0, content='last'))['accepted']
        logger.commit()
        assert pending.result(timeout=5).status_code == 200
    response = client.post(path, headers=headers, json={'sequence': 1, 'content': 'late'})
    assert response.status_code == 409


def test_log_waiting_behind_completion_is_rejected(api):
    client, headers, factory = api
    with factory() as finisher, ThreadPoolExecutor(1) as pool:
        run = finisher.scalar(select(Run).where(Run.id == 'r').with_for_update(key_share=True))
        terminal_log_lock(finisher, run.id)
        pending = pool.submit(client.post, '/api/v1/worker/executions/e/logs-v2',
                              headers=headers, json={'sequence': 0, 'content': 'too late'})
        time.sleep(.05)
        assert not pending.done()
        run.status = 'FAILED'
        run.cleanup_confirmed = True
        finisher.commit()
        assert pending.result(timeout=5).status_code == 409
    with factory() as s:
        assert not s.scalars(select(RunLogChunk)).all()


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
    def record(conn,cursor,sql,params,context,many):statements.append((sql,params))
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
                'global_lock_calls':sum('pg_advisory' in sql and params.get('key') == 82510401
                                        for sql,params in statements if isinstance(params,dict)),
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
