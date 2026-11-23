"""Online admission and PostgreSQL contention; opt-in server must be disposable."""
import importlib
import asyncio
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from backend.app import db_budget as budget
from backend.tests.test_execution_logs_postgres import pg, seed


@pytest.mark.parametrize('kwargs', [
    {'ordinary': 0}, {'control': -1}, {'observation': 0}, {'result': 0}, {'scheduler': 0}, {'processes': True},
    {'processes': 6}, {'pool_timeout': 0}, {'lock_timeout': float('nan')},
    {'request_timeout': 5}, {'request_timeout': .2}, {'lock_timeout': 4},
])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        budget.Settings(**kwargs)


def test_config_environment_and_multiple_process_budget(monkeypatch):
    monkeypatch.setenv('DB_POOL_SIZE', '2')
    monkeypatch.setenv('DB_CONTROL_POOL_SIZE', '1')
    monkeypatch.setenv('API_DB_PROCESS_COUNT', '4')
    monkeypatch.setenv('DB_CONNECTION_BUDGET', '32')
    settings = budget.Settings.environment()
    assert sum(settings.pool_sizes.values()) * settings.processes == 32
    monkeypatch.setenv('DB_CONNECTION_BUDGET', '31')
    with pytest.raises(ValueError):
        budget.Settings.environment()


@pytest.mark.parametrize('path,control', [
    ('/api/v1/worker/heartbeat', True), ('/api/v1/agent/heartbeat', False),
    ('/api/v1/worker/executions/e/service-health', True),
    ('/api/v1/worker/executions/e/service-probe-session', True),
    ('/api/v1/worker/executions/e/service-probe-session/extra', False),
    ('/api/v1/worker/executions/e/service-health/extra', False),
    ('/api/v1/worker/executions/e/logs', False),
    ('/api/v1/worker/slots/s/claim', False),
    ('/api/v1/orchestrations/o/services/s/control', True),
    ('/api/v1/admin/health', False),
])
def test_exact_control_paths(path, control):
    assert budget.control_path(path) is control


def test_admission_rejects_before_app_and_releases_on_failure():
    calls, responses = [], []
    async def app(scope, receive, send):
        calls.append(scope['state']['db_category'])
        assert budget.request_budget.get() is not None
        raise ValueError('handler failed')
    admission = budget.Admission(app, budget.Settings(ordinary=1, control=1))
    async def send(message):
        responses.append(message)
    async def receive():
        return {'type': 'http.request', 'body': b''}
    scope = {'type': 'http', 'method': 'POST', 'path': '/api/v1/worker/slots/s/claim'}
    admission.gates['ordinary'].acquire()
    asyncio.run(admission(scope, receive, send))
    assert not calls and responses[0]['status'] == 503
    # Reserved control admission remains independent of ordinary saturation.
    with pytest.raises(ValueError):
        asyncio.run(admission({**scope, 'path': '/api/v1/worker/heartbeat'}, receive, send))
    assert calls == ['control']
    assert admission.gates['control'].acquire(blocking=False)
    admission.gates['control'].release()
    admission.gates['ordinary'].release()
    with pytest.raises(ValueError):
        asyncio.run(admission(scope, receive, send))
    assert admission.gates['ordinary'].acquire(blocking=False)
    assert budget.request_budget.get() is None


def test_static_and_non_http_bypass_and_get_cannot_claim_control():
    calls = []
    async def app(scope, receive, send):
        calls.append(scope.get('state', {}).get('db_category'))
    admission = budget.Admission(app, budget.Settings())
    for scope in ({'type': 'lifespan'}, {'type': 'http', 'path': '/assets/main.js'},
                  {'type': 'http', 'method': 'GET', 'path': '/api/v1/worker/heartbeat'}):
        asyncio.run(admission(scope, None, None))
    assert calls == [None, 'ordinary', 'ordinary']


@pytest.mark.parametrize('code,reason', [('55P03', 'lock_timeout'), ('57014', 'statement_timeout'),
    ('40P01', 'deadlock'), ('40001', 'serialization_retry'), ('23505', None), ('42P01', None)])
def test_only_known_sql_errors_are_busy(code, reason):
    error = DBAPIError('private sql', {}, SimpleNamespace(sqlstate=code), False)
    assert budget.busy_reason(error) == reason
    assert budget.busy_reason(ValueError()) is None
    assert budget.busy_reason(PoolTimeout()) == 'connection_pool'


@pytest.fixture
def online(pg, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', pg[1])
    monkeypatch.setenv('JWT_SECRET', 'test-only-secret')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    monkeypatch.setenv('DB_BACKPRESSURE_ENABLED', '1')
    monkeypatch.setenv('DB_POOL_SIZE', '1')
    monkeypatch.setenv('DB_CONTROL_POOL_SIZE', '1')
    monkeypatch.setenv('DB_LOCK_TIMEOUT_SECONDS', '.1')
    monkeypatch.setenv('DB_REQUEST_TIMEOUT_SECONDS', '.7')
    from backend.app import main
    from backend.app.core import Worker
    from backend.app.execution import token_hash
    importlib.reload(main)
    factory = seed(pg[0])
    with factory() as s:
        s.get(Worker, 'w').token_hash = token_hash('wk_budget-test')
        s.commit()
    client = TestClient(main.app)
    try:
        yield main, client, {'Authorization': 'Bearer wk_budget-test'}
    finally:
        client.close()
        for factory in main.DatabaseSessions.values():
            factory.kw['bind'].dispose()


def test_control_auth_and_heartbeat_work_while_ordinary_pool_exhausted(online):
    main, client, headers = online
    ordinary, control = main.SessionLocal.kw['bind'], main.ControlSessionLocal.kw['bind']
    with ordinary.connect():
        response = client.post('/api/v1/worker/heartbeat', headers=headers, json={'slot_id': 's'})
        assert response.status_code == 200, response.text
        start = time.monotonic()
        response = client.post('/api/v1/worker/slots/s/claim', headers=headers, json={})
        assert response.status_code == 503, response.text
        assert response.json()['reason'] == 'connection_pool'
        assert time.monotonic() - start < 1
        assert control.pool.checkedout() == 0
    assert ordinary.pool.checkedout() == 0
    assert client.post('/api/v1/worker/heartbeat', headers=headers, json={'slot_id': 's'}).status_code == 200


def test_real_row_lock_times_out_without_connection_leak(online):
    main, client, headers = online
    with main.SessionLocal() as holder:
        holder.execute(text("SELECT id FROM workers WHERE id='w' FOR UPDATE"))
        response = client.post('/api/v1/worker/heartbeat', headers=headers, json={'slot_id': 's'})
        assert response.status_code == 503, response.text
        assert response.json()['reason'] == 'lock_timeout'
        assert response.headers['Retry-After'] == '1'
        assert main.ControlSessionLocal.kw['bind'].pool.checkedout() == 0
    assert client.post('/api/v1/worker/heartbeat', headers=headers, json={'slot_id': 's'}).status_code == 200


def test_claim_does_not_queue_behind_scheduler(online):
    main, client, headers = online
    from backend.app.scheduling import lock
    with main.ControlSessionLocal() as holder:
        lock(holder)
        start = time.monotonic()
        response = client.post('/api/v1/worker/slots/s/claim', headers=headers, json={})
        assert response.status_code == 503, response.text
        assert time.monotonic() - start < .7
        assert main.SessionLocal.kw['bind'].pool.checkedout() == 0


def test_statement_and_cumulative_deadline_do_not_leak_to_background(online):
    main, _, _ = online
    factory = main.SessionLocal
    token = budget.request_budget.set(time.monotonic() + .1)
    try:
        with pytest.raises(DBAPIError) as raised, factory() as s:
            s.execute(text('SELECT pg_sleep(1)'))
        assert budget.busy_reason(raised.value) == 'statement_timeout'
        # PostgreSQL rounds its millisecond timeout down; wait for the actual
        # monotonic budget, rather than assuming cancellation used every ms.
        time.sleep(.01)
        with pytest.raises(budget.DatabaseBusy), factory() as s:
            s.execute(text('SELECT 1'))
    finally:
        budget.request_budget.reset(token)
    with factory() as s:
        assert s.scalar(text('SHOW statement_timeout')) == '0'
        assert s.scalar(text('SHOW lock_timeout')) == '0'
    assert factory.kw['bind'].pool.checkedout() == 0


def test_actual_deadlock_is_transient_and_both_connections_released(pg):
    settings = budget.Settings(ordinary=2, control=1, lock_timeout=2)
    factory, control = budget.factories(pg[1], settings)
    with factory.begin() as s:
        s.execute(text('CREATE TABLE budget_deadlock (id integer primary key)'))
        s.execute(text('INSERT INTO budget_deadlock VALUES (1), (2)'))
    barrier = threading.Barrier(2)
    def run(first, second):
        token = budget.request_budget.set(time.monotonic() + 4)
        try:
            with factory.begin() as s:
                s.execute(text('SELECT id FROM budget_deadlock WHERE id=:id FOR UPDATE'), {'id': first})
                barrier.wait(timeout=2)
                s.execute(text('SELECT id FROM budget_deadlock WHERE id=:id FOR UPDATE'), {'id': second})
            return 'committed'
        except DBAPIError as exc:
            return budget.busy_reason(exc)
        finally:
            budget.request_budget.reset(token)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, 1, 2), pool.submit(run, 2, 1)
            assert sorted([a.result(), b.result()]) == ['committed', 'deadlock']
        assert factory.kw['bind'].pool.checkedout() == 0
    finally:
        factory.kw['bind'].dispose()
        control.kw['bind'].dispose()


def test_http_client_timeout_does_not_leave_sql_running_unbounded(online):
    import socket
    import httpx
    import uvicorn
    from fastapi import Depends
    main, _, _ = online
    entered = threading.Event()
    @main.app.get('/api/v1/test-budget-slow')
    def slow(s=Depends(main.db)):
        entered.set()
        s.execute(text('SELECT pg_sleep(10)'))
        return {'ok': True}
    # The production app has a final SPA catch-all; put the isolated test route
    # before it, otherwise this request only exercises the frontend fallback.
    main.app.router.routes.insert(0, main.app.router.routes.pop())
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(main.app, log_level='critical', lifespan='off'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        until = time.monotonic() + 5
        while not server.started and time.monotonic() < until:
            time.sleep(.01)
        assert server.started
        with pytest.raises(httpx.ReadTimeout):
            httpx.get(f'http://127.0.0.1:{sock.getsockname()[1]}/api/v1/test-budget-slow', timeout=.05, trust_env=False)
        assert entered.is_set()
        engine = main.SessionLocal.kw['bind']
        until = time.monotonic() + 2
        while engine.pool.checkedout() and time.monotonic() < until:
            time.sleep(.01)
        assert engine.pool.checkedout() == 0
        with main.SessionLocal() as s:
            assert s.scalar(text('SELECT 1')) == 1
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_control_burst_fifo_bounded_queue_and_ordinary_isolation():
    async def scenario():
        order = []
        admission = budget.Admission(None, budget.Settings(control=1, ordinary=1))
        assert await admission.acquire('control')
        async def request(index):
            acquired = await admission.acquire('control')
            if acquired:
                order.append(index)
                admission.gates['control'].release()
            return acquired
        tasks = []
        for index in range(4):
            tasks.append(asyncio.create_task(request(index)))
            await asyncio.sleep(0)
        assert len(admission.waiters) == 4
        assert not await admission.acquire('control')  # queue full
        assert await admission.acquire('ordinary')
        assert not await admission.acquire('ordinary')  # no ordinary waiting
        admission.gates['ordinary'].release()
        admission.gates['control'].release()
        assert all(await asyncio.gather(*tasks))
        assert order == [0, 1, 2, 3]
        assert not admission.waiters
        assert admission.gates['control'].acquire(blocking=False)
    asyncio.run(scenario())


def test_control_wait_timeout_and_cancellation_leave_no_ticket_or_permit():
    async def scenario():
        admission = budget.Admission(None, budget.Settings(control=1, pool_timeout=.02))
        assert await admission.acquire('control')
        began = time.monotonic()
        assert not await admission.acquire('control')
        assert time.monotonic() - began < .2
        assert not admission.waiters
        task = asyncio.create_task(admission.acquire('control'))
        await asyncio.sleep(0)
        assert len(admission.waiters) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not admission.waiters
        assert not admission.gates['control'].acquire(blocking=False)
        admission.gates['control'].release()
        assert await admission.acquire('control')
    asyncio.run(scenario())


def test_control_wait_counts_against_request_budget_and_cancellation_releases():
    async def scenario():
        began = time.monotonic()
        entered = asyncio.Event()
        async def app(scope, receive, send):
            assert budget.request_budget.get() <= began + .7 + .01
            entered.set()
            await asyncio.Event().wait()
        admission = budget.Admission(app, budget.Settings(control=1, request_timeout=.7, lock_timeout=.1))
        admission.gates['control'].acquire()
        task = asyncio.create_task(admission(
            {'type':'http', 'method':'POST', 'path':'/api/v1/worker/heartbeat'}, None, None))
        await asyncio.sleep(.03)
        admission.gates['control'].release()
        await asyncio.wait_for(entered.wait(), .3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert admission.gates['control'].acquire(blocking=False)
        assert not admission.waiters
        assert budget.request_budget.get() is None
    asyncio.run(scenario())


@pytest.mark.parametrize('path,category', [
    ('/api/v1/agent/heartbeat','observation'),
    ('/api/v1/worker/executions/e/complete','control'),
    ('/api/v1/worker/executions/e/cleanup','control'),
    ('/api/v1/agent/stop-permit','control'),
    ('/api/v1/jobs/j/cancel','control'),
    ('/api/v1/batches/b/control','control'),
    ('/api/v1/admin/services/s/control','control'),
    ('/api/v1/worker/executions/e/episodes','result'),
    ('/api/v1/worker/executions/e/task-result','result'),
    ('/api/v1/worker/executions/e/logs','ordinary'),
    ('/api/v1/worker/executions/e/complete/extra','ordinary'),
    ('/api/v1/jobs/j/cancel/extra','ordinary'),
])
def test_semantic_categories_and_method_boundary(path, category):
    assert budget.request_category(path) == category
    assert budget.request_category(path, 'GET') == 'ordinary'
    assert budget.request_category(path, 'PUT') == 'ordinary'


def test_completion_and_cleanup_survive_other_pool_exhaustion(online):
    from contextlib import ExitStack
    main, client, headers = online
    with ExitStack() as stack:
        for name in ('ordinary', 'observation', 'result', 'scheduler'):
            factory = main.DatabaseSessions[name]
            for _ in range(factory.kw['bind'].pool.size()):
                stack.enter_context(factory.kw['bind'].connect())
        response = client.post('/api/v1/worker/executions/e/complete', headers=headers,
                               json={'status':'FAILED','exit_code':1,'error':'test'})
        assert response.status_code == 200, response.text
        response = client.post('/api/v1/worker/executions/e/cleanup', headers=headers)
        assert response.status_code == 200, response.text
        assert main.ControlSessionLocal.kw['bind'].pool.checkedout() == 0


def test_scheduler_pool_survives_online_pool_exhaustion(online):
    from contextlib import ExitStack
    main, _, _ = online
    with ExitStack() as stack:
        for name, factory in main.DatabaseSessions.items():
            if name == 'scheduler':
                continue
            for _ in range(factory.kw['bind'].pool.size()):
                stack.enter_context(factory.kw['bind'].connect())
        with main.SchedulerSessionLocal() as session:
            assert session.scalar(text('SELECT 1')) == 1
        assert main.SchedulerSessionLocal.kw['bind'].pool.checkedout() == 0


def test_admission_bounds_all_http_and_preserves_thread_headroom():
    import anyio.to_thread
    async def scenario():
        active, release = asyncio.Event(), asyncio.Event()
        calls=[]
        async def app(scope, receive, send):
            calls.append(scope['state']['db_category'])
            active.set()
            await release.wait()
        admission=budget.Admission(app,budget.Settings(ordinary=1,control=1,observation=1,result=1))
        limiter=anyio.to_thread.current_default_thread_limiter()
        old=limiter.total_tokens
        limiter.total_tokens=1
        scope={'type':'http','path':'/assets/app.js','method':'GET'}
        task=asyncio.create_task(admission(scope,None,None))
        await active.wait()
        assert limiter.total_tokens >= admission.thread_capacity
        responses=[]
        async def send(message): responses.append(message)
        await admission(scope,None,send)
        assert responses[0]['status']==503
        assert await admission.acquire('control')
        admission.gates['control'].release()
        assert await admission.acquire('observation')
        admission.gates['observation'].release()
        release.set()
        await task
        limiter.total_tokens=old
        assert calls==['ordinary']
    asyncio.run(scenario())


def test_sql_budget_applies_transaction_local_configuration(online):
    main,_,_=online
    with main.SessionLocal() as session:
        token=budget.request_budget.set(time.monotonic()+.6)
        try:
            assert session.scalar(text('SELECT 42'))==42
            assert session.scalar(text("SHOW statement_timeout")) != "0"
        finally:
            budget.request_budget.reset(token)


def test_noncontrol_sync_handlers_cannot_starve_control_dispatch():
    import anyio.to_thread
    async def scenario():
        release=threading.Event()
        entered=[]
        async def app(scope,receive,send):
            category=scope['state']['db_category']
            if category=='control':
                assert await anyio.to_thread.run_sync(lambda:'control-ran')=='control-ran'
                release.set()
            else:
                entered.append(category)
                assert await anyio.to_thread.run_sync(release.wait,2)
        admission=budget.Admission(app,budget.Settings(ordinary=1,control=1,observation=1,result=1))
        limiter=anyio.to_thread.current_default_thread_limiter()
        old=limiter.total_tokens
        limiter.total_tokens=1
        paths=['/assets/app.js','/api/v1/agent/heartbeat','/api/v1/worker/executions/e/episodes']
        tasks=[asyncio.create_task(admission({'type':'http','method':'POST','path':path},None,None)) for path in paths]
        try:
            async def wait_entered():
                while len(entered)<3: await asyncio.sleep(.001)
            await asyncio.wait_for(wait_entered(),1)
            await asyncio.wait_for(admission({'type':'http','method':'POST','path':'/api/v1/worker/heartbeat'},None,None),1)
            await asyncio.gather(*tasks)
        finally:
            release.set()
            await asyncio.gather(*tasks,return_exceptions=True)
            limiter.total_tokens=old
    asyncio.run(scenario())


def test_liveness_is_independent_of_saturated_admission():
    async def scenario():
        calls=[]
        async def app(scope,receive,send):calls.append(scope['path'])
        admission=budget.Admission(app,budget.Settings(ordinary=1))
        admission.gates['ordinary'].acquire()
        try:
            await admission({'type':'http','method':'GET','path':'/healthz'},None,None)
        finally:admission.gates['ordinary'].release()
        assert calls==['/healthz']
    asyncio.run(scenario())


def test_disabled_admission_preserves_category_and_deadline_without_entry_limit():
    async def scenario():
        calls = []
        async def app(scope, receive, send):
            calls.append((scope['state']['db_category'], budget.request_budget.get()))

        admission = budget.Admission(app, budget.Settings(ordinary=1, request_timeout=1),
                                     admission_enabled=False)
        assert admission.gates['ordinary'].acquire(blocking=False)
        try:
            scope = {'type': 'http', 'method': 'POST', 'path': '/api/v1/worker/slots/s/claim'}
            await asyncio.gather(*(admission(dict(scope), None, None) for _ in range(3)))
            assert len(calls) == 3
            assert all(category == 'ordinary' and deadline is not None for category, deadline in calls)
            assert budget.request_budget.get() is None
            assert not admission.gates['ordinary'].acquire(blocking=False)
        finally:
            admission.gates['ordinary'].release()
    asyncio.run(scenario())
