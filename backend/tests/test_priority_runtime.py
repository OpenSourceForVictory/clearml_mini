import asyncio
from dataclasses import replace
import json
import importlib.util
import sys
from pathlib import Path
import re
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
import yaml

from backend.app import db_budget, priority_runtime as runtime
from backend.app.priority_routes import manifest, ROLES
from backend.tests.test_execution_logs_postgres import pg


def settings(role='interaction', **kwargs):
    return runtime.Settings(role, role == 'worker-data', **kwargs)


def test_each_role_has_only_its_pools_and_one_scheduler():
    profiles = {role: settings(role).active_pool_sizes for role in runtime.ROLE_POOLS}
    assert sum(sum(p.values()) for p in profiles.values()) == 80
    scaled = {role: settings(role, pool_sizes=runtime.SCALED_POOLS, processes=5, control_replicas=2).active_pool_sizes
              for role in runtime.ROLE_POOLS}
    assert sum(sum(p.values()) for p in scaled.values()) + scaled['execution-control']['execution_control'] == 80
    assert settings().configured_connections == 80
    assert settings(pool_sizes=runtime.SCALED_POOLS, processes=5, control_replicas=2).configured_connections == 80
    with pytest.raises(ValueError, match='budget'):
        settings('execution-control', pool_sizes=runtime.DEFAULT_POOLS, processes=5, control_replicas=2)
    assert [role for role in profiles if 'scheduler' in profiles[role]] == ['worker-data']
    assert len({settings(role).fingerprint for role in profiles}) == 1
    for role in profiles:
        middleware = runtime.Admission(None, settings(role))
        assert middleware.thread_capacity >= sum(middleware.available.values()) + 4


@pytest.mark.parametrize('kwargs', [
    {'route_group':'bad'}, {'processes':2}, {'processes':True},
    {'control_replicas':0}, {'control_replicas':True}, {'scheduler_enabled':True},
    {'pool_sizes':{}}, {'pool_sizes':list(runtime.DEFAULT_POOLS)},
    {'pool_sizes':dict(runtime.DEFAULT_POOLS, page_read=0)},
    {'pool_sizes':dict(runtime.DEFAULT_POOLS, page_read=True)},
    {'pool_sizes':dict(runtime.DEFAULT_POOLS, scheduler=2), 'connection_budget':81},
    {'connection_budget':79}, {'connection_headroom':0}, {'pool_timeout':float('nan')},
    {'pool_timeout':4}, {'lock_timeout':4}, {'request_timeout':5},
    {'long_request_timeout':20}, {'long_request_timeout':61},
    {'peers':{}}, {'peers':dict(runtime.PEERS, interaction='file:///tmp/test')},
    {'peers':dict(runtime.PEERS, interaction='http://user:secret@host')},
    {'peers':dict(runtime.PEERS, interaction='http://host/path')},
])
def test_rejects_invalid_role_and_global_budget(kwargs):
    with pytest.raises(ValueError):
        replace(settings(), **kwargs)


def test_environment_manifest_and_config_drift(monkeypatch):
    monkeypatch.setenv('API_ROUTE_GROUP', 'worker-data')
    monkeypatch.setenv('API_DB_PROCESS_COUNT', '4')
    monkeypatch.setenv('API_CONTROL_REPLICAS', '1')
    monkeypatch.setenv('SCHEDULER_ENABLED', '1')
    monkeypatch.setenv('PRIORITY_API_POOL_SIZES', '')
    assert runtime.Settings.environment().fingerprint == settings('worker-data').fingerprint
    pools = dict(runtime.DEFAULT_POOLS, page_read=9)
    monkeypatch.setenv('PRIORITY_API_POOL_SIZES', json.dumps(pools))
    assert runtime.Settings.environment().fingerprint != settings('worker-data').fingerprint
    monkeypatch.setenv('PRIORITY_API_POOL_SIZES', '')
    monkeypatch.setenv('API_DB_PROCESS_COUNT', '5')
    monkeypatch.setenv('API_CONTROL_REPLICAS', '2')
    assert runtime.Settings.environment().pool_sizes == runtime.SCALED_POOLS
    assert runtime.Settings.environment().fingerprint != settings('worker-data').fingerprint
    monkeypatch.setenv('PRIORITY_API_POOL_SIZES', json.dumps(runtime.DEFAULT_POOLS))
    with pytest.raises(ValueError, match='budget'):
        runtime.Settings.environment()
    monkeypatch.setenv('PRIORITY_API_POOL_SIZES', '')
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')
    with pytest.raises(ValueError, match='scheduler'):
        runtime.Settings.environment()


async def invoke(middleware, path, method='GET', kind='http'):
    messages = []
    async def send(message):
        messages.append(message)
    async def receive():
        return {'type':'http.request', 'body':b'', 'more_body':False}
    await middleware({'type':kind, 'path':path, 'method':method}, receive, send)
    return messages


async def respond(scope, receive, send):
    await send({'type':'http.response.start', 'status':200, 'headers':[]})
    await send({'type':'http.response.body', 'body':b'ok'})


def test_every_registered_method_is_rejected_before_wrong_role_handler():
    async def run():
        for role in runtime.ROLE_POOLS:
            middleware = runtime.Admission(respond, settings(role))
            for method, path, category, _, _ in manifest():
                path = re.sub(r'\{[^}]+\}', 'sample-id', path)
                result = await invoke(middleware, path, method)
                assert result[0]['status'] == (200 if ROLES[category] in (None, role) else 404)
            result = await invoke(middleware, '/unknown')
            assert result[0]['status'] == (200 if role == 'interaction' else 404)
            assert (await invoke(middleware, '/', kind='lifespan'))[0]['status'] == 200
    asyncio.run(run())


def test_saturated_reads_and_long_requests_preserve_action_capacity_and_cancel_releases():
    async def run():
        pools = dict(runtime.DEFAULT_POOLS, page_read=1, user_long=1)
        started, release = asyncio.Event(), asyncio.Event()
        budgets = []
        async def app(scope, receive, send):
            category = scope.get('state', {}).get('db_category')
            budgets.append((category, db_budget.request_budget.get()))
            if category in ('page_read', 'user_long'):
                started.set()
                await release.wait()
            await respond(scope, receive, send)
        middleware = runtime.Admission(app, settings(pool_sizes=pools, pool_timeout=.02))
        active = asyncio.create_task(invoke(middleware, '/api/v1/batches'))
        await started.wait()
        assert (await invoke(middleware, '/api/v1/batches'))[0]['status'] == 503
        assert (await invoke(middleware, '/api/v1/batches', 'POST'))[0]['status'] == 200
        waiting = asyncio.create_task(invoke(middleware, '/api/v1/batches'))
        await asyncio.sleep(.005)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not middleware.waiters['page_read']
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert middleware.available['page_read'] == 1
        started.clear()
        long = asyncio.create_task(invoke(middleware, '/api/v1/batches/preflight', 'POST'))
        await started.wait()
        assert budgets[-1][1] - time.monotonic() > 20
        assert (await invoke(middleware, '/api/v1/batches', 'POST'))[0]['status'] == 200
        release.set()
        await long
        assert middleware.available == {k:v for k,v in pools.items() if k in runtime.ROLE_POOLS['interaction']}
    asyncio.run(run())


def test_admission_fifo_bounded_queue_and_handler_failure():
    async def run():
        middleware = runtime.Admission(respond, settings(pool_sizes=dict(runtime.DEFAULT_POOLS, user_action=1), pool_timeout=.1))
        assert await middleware.acquire('user_action')
        tasks = [asyncio.create_task(middleware.acquire('user_action')) for _ in range(4)]
        await asyncio.sleep(.005)
        assert not await middleware.acquire('user_action')
        middleware.available['user_action'] += 1
        assert await tasks[0]
        for task in tasks[1:]:
            task.cancel()
        await asyncio.gather(*tasks[1:], return_exceptions=True)
        assert not middleware.waiters['user_action']
        middleware.available['user_action'] += 1
        async def broken(*args):
            raise RuntimeError('test handler')
        middleware.app = broken
        with pytest.raises(RuntimeError):
            await invoke(middleware, '/api/v1/batches', 'POST')
        assert middleware.available['user_action'] == 1
        assert db_budget.request_budget.get() is None
    asyncio.run(run())


@pytest.mark.parametrize('failure', [None, 'role', 'budget', 'status', 'json', 'json_type', 'connection', 'missing'])
def test_health_checks_every_role_and_configuration(failure):
    expected = settings()
    def handle(request):
        role = request.url.host.removesuffix('-api')
        if role == 'bulk-transfer' and failure:
            if failure == 'connection':
                raise httpx.ConnectError('test offline')
            if failure == 'json':
                return httpx.Response(200, text='bad json')
            if failure == 'json_type':
                return httpx.Response(200, json=[])
            return httpx.Response(503 if failure == 'status' else 200, json={
                'role':'wrong' if failure == 'role' else role,
                'budget':'wrong' if failure == 'budget' else expected.fingerprint})
        return httpx.Response(200, json={'role':role, 'budget':expected.fingerprint})
    peers = {k:v for k,v in runtime.PEERS.items() if not (failure == 'missing' and k == 'bulk-transfer')}
    response = asyncio.run(runtime.deployment_health(expected, peers=peers, transport=httpx.MockTransport(handle)))
    assert response.status_code == (200 if failure is None else 503)


def test_priority_compose_has_single_scheduler_and_preserves_public_port():
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor('!reset', lambda loader, node: loader.construct_sequence(node))
    config = yaml.load((Path(__file__).resolve().parents[2]/'deploy/priority-api-compose.yml').read_text(), Loader=Loader)
    services = config['services']
    roles = {}
    for name in ('api', 'execution-control-api', 'worker-data-api', 'bulk-transfer-api'):
        service = services[name]
        env = service['environment']
        assert service['ports'] == []
        profile = runtime.Settings(env['API_ROUTE_GROUP'], env['SCHEDULER_ENABLED'] == '1',
                                   pool_sizes=runtime.SCALED_POOLS, processes=int(env['API_DB_PROCESS_COUNT'].split(':-')[-1].rstrip('}')),
                                   control_replicas=int(env['API_CONTROL_REPLICAS']))
        roles[profile.route_group] = profile
    assert set(roles) == set(runtime.ROLE_POOLS)
    assert [r for r,p in roles.items() if p.scheduler_enabled] == ['worker-data']
    assert services['api-gateway']['ports'] == ['${API_BIND_HOST:-127.0.0.1}:${API_BIND_PORT:-8000}:8000']
    assert 'interaction-api' in services['api']['networks']['default']['aliases']
    assert services['execution-control-api']['scale'] == 2
    # Onboarding reads both interaction metadata and the bulk-transfer bundle.
    assert runtime.ROLE_POOLS['bulk-transfer'] == ('bulk_transfer',)
    assert services['onboarding']['command'][-1] == 'http://api-gateway:8000'
    from backend.app.priority_routes import role_for
    assert role_for('/api/v1/admin/nodes', 'GET') == 'interaction'
    assert role_for('/api/v1/admin/node-installation-bundle', 'GET') == 'bulk-transfer'


@pytest.mark.parametrize('enabled', [None, '1'])
def test_recovery_flag_reaches_every_api_role(tmp_path, enabled):
    """A .env-only flag is ineffective unless Compose passes it to every API role."""
    import os
    import shutil
    import subprocess

    if not shutil.which('docker') or subprocess.run(['docker', 'compose', 'version'],
            capture_output=True).returncode:
        pytest.skip('Docker Compose is required for deployment rendering')
    root = Path(__file__).resolve().parents[2]
    env_file = tmp_path/'compose.env'
    env_file.write_text('')
    env = {'PATH':os.environ['PATH'], 'HOME':str(tmp_path),
           'POSTGRES_PASSWORD':'test-only', 'JWT_SECRET':'test-only',
           'ADMIN_USERNAME':'test-only', 'ADMIN_PASSWORD':'test-only'}
    if enabled is not None:
        env['RECOVERY_EXECUTION_ENABLED'] = enabled
    command = ['docker', 'compose', '--env-file', str(env_file), '-p', 'recovery-config-test',
               '-f', str(root/'deploy/compose.yml'), '-f', str(root/'deploy/priority-api-compose.yml'),
               'config', '--format', 'json']
    config = json.loads(subprocess.run(command, env=env, capture_output=True,
                                      text=True, check=True).stdout)
    for role in ('api', 'execution-control-api', 'worker-data-api', 'bulk-transfer-api'):
        assert config['services'][role]['environment']['RECOVERY_EXECUTION_ENABLED'] == (enabled or '0')


def test_postgres_budget_and_pool_exhaustion_are_role_local(pg):
    pools = dict.fromkeys(runtime.DEFAULT_POOLS, 1)
    factories = {role: db_budget.factories(pg[1], settings(role, pool_sizes=pools, pool_timeout=.02), isolated=True)
                 for role in runtime.ROLE_POOLS}
    try:
        for role, sessions in factories.items():
            assert set(sessions) == set(runtime.ROLE_POOLS[role])
            runtime.check_database_budget(next(iter(sessions.values())), settings(role, pool_sizes=pools))
        with factories['bulk-transfer']['bulk_transfer']() as occupied:
            occupied.execute(text('SELECT 1'))
            with pytest.raises(PoolTimeout):
                with factories['bulk-transfer']['bulk_transfer']() as overflow:
                    overflow.execute(text('SELECT 1'))
            for role, category in (('execution-control','execution_control'), ('interaction','user_action'), ('worker-data','worker_claim')):
                with factories[role][category]() as available:
                    assert available.scalar(text('SELECT 1')) == 1
        with pytest.raises(ValueError, match='capacity'):
            runtime.check_database_budget(factories['interaction']['user_action'], settings(connection_headroom=10000))
        with pytest.raises(ValueError, match='capacity'):
            runtime.check_database_budget(factories['interaction']['user_action'], settings(connection_headroom=1))
    finally:
        for sessions in factories.values():
            for factory in sessions.values():
                assert factory.kw['bind'].pool.checkedout() == 0
                factory.kw['bind'].dispose()


def test_priority_startup_rejects_sqlite():
    engine = create_engine('sqlite://')
    try:
        with pytest.raises(ValueError, match='PostgreSQL'):
            runtime.check_database_budget(sessionmaker(engine), settings())
    finally:
        engine.dispose()


def test_priority_pools_do_not_hide_cross_role_row_lock_waits(pg):
    profiles = settings('execution-control', lock_timeout=.05, request_timeout=.5)
    factories = db_budget.factories(pg[1], profiles, isolated=True)
    factory = factories['execution_control']
    with pg[0].begin() as c:
        c.execute(text('CREATE TABLE priority_lock_test (id integer primary key, value integer)'))
        c.execute(text('INSERT INTO priority_lock_test VALUES (1,0),(2,0)'))
    try:
        with pg[0].begin() as holder:
            holder.execute(text('UPDATE priority_lock_test SET value=1 WHERE id=1'))
            token = db_budget.request_budget.set(time.monotonic()+.5)
            try:
                with factory() as session:
                    with pytest.raises(DBAPIError) as caught:
                        session.execute(text('UPDATE priority_lock_test SET value=2 WHERE id=1'))
                    assert db_budget.busy_reason(caught.value) == 'lock_timeout'
                with factory.begin() as session:
                    session.execute(text('UPDATE priority_lock_test SET value=2 WHERE id=2'))
            finally:
                db_budget.request_budget.reset(token)
        with factory() as session:
            assert session.scalar(text('SHOW statement_timeout')) == '0'
    finally:
        factory.kw['bind'].dispose()


@pytest.mark.parametrize('role,path,method', [
    ('interaction', '/api/v1/batches', 'POST'),
    ('execution-control', '/api/v1/worker/heartbeat', 'POST'),
    ('worker-data', '/api/v1/worker/slots/s/claim', 'POST'),
    ('bulk-transfer', '/api/v1/worker/executions/e/logs-v2', 'POST'),
])
def test_actual_app_role_health_auth_session_and_scheduler(pg, monkeypatch, role, path, method):
    from fastapi.testclient import TestClient
    from starlette.requests import Request
    from backend.app.core import Base
    Base.metadata.create_all(pg[0])
    monkeypatch.setenv('DATABASE_URL', pg[1])
    monkeypatch.setenv('JWT_SECRET', 'isolated-priority-test-only')
    monkeypatch.setenv('DB_BACKPRESSURE_ENABLED', '1')
    monkeypatch.setenv('DB_ADMISSION_ENABLED', '1')
    monkeypatch.setenv('API_DB_PROCESS_COUNT', '5')
    monkeypatch.setenv('API_CONTROL_REPLICAS', '2')
    monkeypatch.setenv('API_ROUTE_GROUP', role)
    monkeypatch.setenv('SCHEDULER_ENABLED', '1' if role == 'worker-data' else '0')
    name = 'backend.app._priority_test_main'
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1]/'app/main.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    calls = []
    def scheduler(factory, stop):
        calls.append(factory)
        stop.wait(5)
    monkeypatch.setattr(module, 'loop', scheduler)
    try:
        with TestClient(module.app) as client:
            health = client.get('/healthz')
            assert health.status_code == 200 and health.json()['role'] == role
            assert client.get('/readyz').status_code == 200
            assert client.request(method, path).status_code == 401
            wrong = '/api/v1/batches' if role != 'interaction' else '/api/v1/worker/heartbeat'
            assert client.post(wrong).status_code == 404
            for category, factory in module.DatabaseSessions.items():
                if category == 'scheduler':
                    continue
                generator = module.db(Request({'type':'http', 'state':{'db_category':category}}))
                try:
                    assert next(generator).bind is factory.kw['bind']
                finally:
                    generator.close()
        assert calls == ([module.SchedulerSessionLocal] if role == 'worker-data' else [])
    finally:
        for factory in module.DatabaseSessions.values():
            factory.kw['bind'].dispose()
