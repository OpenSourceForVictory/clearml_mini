"""HTTP and DB capacity are separate, while category isolation stays intact."""
import asyncio
from dataclasses import replace
import json
import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError
from backend.app import priority_runtime as runtime, db_budget
from backend.tests.test_priority_runtime import settings, invoke, respond
from backend.tests.test_execution_logs_postgres import pg


def test_defaults_preserve_existing_limits():
    s=settings()
    for name,size in s.active_pool_sizes.items():
        assert s.admission_size(name)==size
        assert s.queue_size(name)==4*size
        assert s.queue_seconds(name)==s.pool_seconds(name)==.25
    assert s.request_seconds('page_read')==4
    assert s.request_seconds('user_long')==30


def test_http_capacity_and_thread_allowance_do_not_increase_db_connections():
    s=settings(admission_limits={'page_read':50},queue_limits={'page_read':100},
               queue_waits={'page_read':2},pool_waits={'page_read':1},request_budgets={'page_read':12})
    admission=runtime.Admission(None,s)
    assert admission.available['page_read']==50
    assert admission.thread_capacity==68
    assert s.active_pool_sizes['page_read']==10 and s.configured_connections==80
    assert s.queue_size('page_read')==100 and s.pool_seconds('page_read')==1
    assert s.fingerprint!=settings().fingerprint


@pytest.mark.parametrize('kwargs',[
    {'admission_limits':{'unknown':2}}, {'admission_limits':{'scheduler':2}},
    {'admission_limits':{'page_read':0}}, {'admission_limits':{'page_read':True}},
    {'queue_limits':{'page_read':-1}}, {'queue_limits':[]},
    {'queue_waits':{'page_read':float('inf')}}, {'queue_waits':{'page_read':True}},
    {'pool_waits':{'page_read':0}}, {'request_budgets':{'page_read':None}},
    {'queue_waits':{'page_read':3},'pool_waits':{'page_read':1}},
    {'request_budgets':{'execution_control':5}}, {'request_budgets':{'page_read':61}},
    {'request_budgets':{'page_read':.4}}, {'pool_waits':{'page_read':-1}},
])
def test_invalid_category_capacity_rejected(kwargs):
    with pytest.raises(ValueError):settings(**kwargs)


def test_environment_capacity_and_fingerprint(monkeypatch):
    monkeypatch.setenv('API_ROUTE_GROUP','interaction')
    monkeypatch.setenv('SCHEDULER_ENABLED','0')
    monkeypatch.setenv('API_DB_PROCESS_COUNT','4')
    monkeypatch.setenv('API_CONTROL_REPLICAS','1')
    values={'ADMISSION_LIMITS':{'page_read':20},'QUEUE_LIMITS':{'page_read':30},
            'QUEUE_WAITS':{'page_read':1},'POOL_WAITS':{'page_read':.5},
            'REQUEST_BUDGETS':{'page_read':10}}
    for suffix,value in values.items():monkeypatch.setenv('PRIORITY_API_'+suffix,json.dumps(value))
    s=runtime.Settings.environment()
    assert s.admission_size('page_read')==20 and s.pool_seconds('page_read')==.5
    assert s.request_seconds('page_read')==10 and s.queue_seconds('page_read')==1
    assert s.fingerprint==replace(s,route_group='bulk-transfer').fingerprint


def test_zero_queue_and_wait_do_not_disable_available_capacity():
    async def run():
        m=runtime.Admission(respond,settings(admission_limits={'page_read':1},
            queue_limits={'page_read':0},queue_waits={'page_read':0}))
        assert await m.acquire('page_read')
        assert not await m.acquire('page_read')
        assert not m.waiters['page_read']
        m.available['page_read']+=1
        assert await m.acquire('page_read')
    asyncio.run(run())


def test_admission_diagnostics_report_accepted_and_rejected_waits(monkeypatch):
    messages = []
    monkeypatch.setattr(db_budget.LOG, 'debug', lambda template, *args: messages.append(template % args))
    monkeypatch.setattr(db_budget.LOG, 'warning', lambda template, *args: messages.append(template % args))

    async def run():
        m = runtime.Admission(respond, settings(admission_limits={'page_read': 1},
                                                queue_limits={'page_read': 0}))
        assert (await invoke(m, '/api/v1/batches'))[0]['status'] == 200
        assert await m.acquire('page_read')
        assert (await invoke(m, '/api/v1/batches'))[0]['status'] == 503
        m.available['page_read'] += 1

    asyncio.run(run())
    assert any('admission accepted category=page_read wait_ms=' in item for item in messages)
    assert any('admission rejected category=page_read reason=concurrency wait_ms=' in item for item in messages)


def test_web_waits_longer_without_consuming_action_or_db_allowance():
    async def run():
        s=settings(admission_limits={'page_read':1},queue_waits={'page_read':.4},
                   pool_waits={'page_read':.01},request_budgets={'page_read':8})
        m=runtime.Admission(respond,s)
        assert await m.acquire('page_read')
        waiting=asyncio.create_task(invoke(m,'/api/v1/batches'))
        await asyncio.sleep(.27)
        assert not waiting.done()
        assert (await invoke(m,'/api/v1/jobs/j/cancel','POST'))[0]['status']==200
        m.available['page_read']+=1
        assert (await waiting)[0]['status']==200
        assert m.available['page_read']==1
        assert not m.waiters['page_read']
    asyncio.run(run())


def test_custom_request_deadline_and_cancel_release():
    async def run():
        entered=asyncio.Event()
        async def app(scope,receive,send):
            assert db_budget.request_budget.get()-__import__('time').monotonic()>7
            entered.set();await asyncio.Event().wait()
        m=runtime.Admission(app,settings(request_budgets={'page_read':8}))
        task=asyncio.create_task(invoke(m,'/api/v1/batches'))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert m.available['page_read']==10
        assert db_budget.request_budget.get() is None
    asyncio.run(run())


def test_real_db_pool_wait_is_independent_and_does_not_overflow(pg):
    s=settings(pool_sizes=dict(runtime.DEFAULT_POOLS,page_read=1),
               admission_limits={'page_read':4},pool_waits={'page_read':.06},queue_waits={'page_read':.3})
    factories=db_budget.factories(pg[1],s,isolated=True)
    try:
        engine=factories['page_read'].kw['bind']
        assert engine.pool.size()==1 and engine.pool.timeout()==.06
        with engine.connect() as first:
            assert first.scalar(text('SELECT 1'))==1
            with pytest.raises(TimeoutError):
                with engine.connect():pass
        with engine.connect() as again:assert again.scalar(text('SELECT 1'))==1
    finally:
        for factory in factories.values():factory.kw['bind'].dispose()


def test_opt_in_pool_diagnostics_measure_all_checkouts_without_sensitive_data(pg, monkeypatch):
    monkeypatch.setenv('DB_BUDGET_DIAGNOSTICS', '1')
    messages = []
    monkeypatch.setattr(db_budget.LOG, 'debug', lambda template, *args: messages.append(template % args))
    monkeypatch.setattr(db_budget.LOG, 'warning', lambda template, *args: messages.append(template % args))
    s = settings(pool_sizes=dict(runtime.DEFAULT_POOLS, page_read=1),
                 pool_waits={'page_read': .02})
    factories = db_budget.factories(pg[1], s, isolated=True)
    try:
        engine = factories['page_read'].kw['bind']
        assert isinstance(engine.pool, db_budget.DiagnosticQueuePool)
        with engine.connect() as first:
            assert first.scalar(text('SELECT 1')) == 1
            with pytest.raises(TimeoutError):
                with engine.connect():
                    pass
        with engine.connect() as again:
            assert again.scalar(text('SELECT 1')) == 1
        successes = [item for item in messages if 'outcome=ok' in item]
        failures = [item for item in messages if 'outcome=timeout' in item]
        assert len(successes) == 2 and len(failures) == 1
        assert all('category=page_read' in item and 'wait_ms=' in item for item in messages)
        assert all('postgresql' not in item and 'SELECT' not in item for item in messages)
    finally:
        for factory in factories.values():
            factory.kw['bind'].dispose()


def test_checked_in_team_profile_validates_without_raising_db_budget():
    from pathlib import Path
    profile=json.loads(Path('deploy/profiles/team-capacity-candidate.json').read_text())
    s=settings(pool_sizes=runtime.SCALED_POOLS,processes=5,control_replicas=2,**profile)
    assert s.configured_connections==80
    assert s.admission_size('page_read')>s.pool_sizes['page_read']
    assert s.queue_seconds('page_read')>s.queue_seconds('execution_control')
    assert s.request_seconds('execution_control')<5<s.request_seconds('page_read')


@pytest.mark.parametrize('replicas',[0,9,True,None,'2'])
def test_invalid_interaction_replicas_rejected(replicas):
    with pytest.raises(ValueError):settings(interaction_replicas=replicas)


def test_interaction_replica_connections_are_counted_and_scheduler_stays_unique():
    pools=dict(runtime.SCALED_POOLS,user_action=3,user_long=1,page_read=16)
    s=settings(pool_sizes=pools,control_replicas=2,interaction_replicas=4,processes=8,connection_budget=160)
    assert s.configured_connections==136
    with pytest.raises(ValueError,match='budget'):replace(s,connection_budget=135)
    with pytest.raises(ValueError,match='process count'):replace(s,processes=5)
    assert s.active_pool_sizes=={'user_action':3,'user_long':1,'page_read':16}
    assert 'scheduler' not in s.active_pool_sizes
    assert replace(s,route_group='worker-data',scheduler_enabled=True).active_pool_sizes['scheduler']==1


def test_multi_interaction_gateway_routes_all_user_methods_to_same_pool():
    from backend.tests.control_plane_load import render_priority_gateway_config
    ports={'interaction':10001,'execution-control':10002,'worker-data':10003,'bulk-transfer':10004}
    value=render_priority_gateway_config(ports,10000,10005,[10006,10007])
    assert 'upstream interaction-load-pool { server 127.0.0.1:10001; server 127.0.0.1:10006; server 127.0.0.1:10007;' in value
    assert 'GET:/api/v1/batches interaction-load-pool;' in value
    assert 'POST:/api/v1/batches interaction-load-pool;' in value
    assert 'POST:/api/v1/worker/heartbeat execution-control-load-pool;' in value


def test_team_profile_counts_all_twelve_processes_and_env_matches():
    from pathlib import Path
    from dotenv import dotenv_values
    profile=json.loads(Path('deploy/profiles/team-capacity-100.json').read_text())
    s=settings(processes=12,control_replicas=2,**profile)
    assert s.configured_connections==168
    assert s.connection_budget+s.connection_headroom==200
    assert s.interaction_replicas==8
    env=dotenv_values('deploy/profiles/team-capacity-100.env.example')
    assert int(env['API_DB_PROCESS_COUNT'])==s.processes
    for key,value in profile.items():
        names={'connection_budget':'DB_CONNECTION_BUDGET','interaction_replicas':'API_INTERACTION_REPLICAS'}
        name=names.get(key,'PRIORITY_API_'+key.upper())
        assert json.loads(env[name])==value


@pytest.mark.parametrize('role,category,path',[
    ('bulk-transfer','bulk_transfer','/api/v1/worker/executions/e/logs'),
    ('worker-data','worker_observation','/api/v1/agent/heartbeat'),
])
def test_team_burst_waits_without_borrowing_control_capacity(role,category,path):
    from pathlib import Path
    profile=json.loads(Path('deploy/profiles/team-capacity-100.json').read_text())
    async def run():
        s=settings(role,processes=12,control_replicas=2,**profile)
        m=runtime.Admission(respond,s)
        for _ in range(12):assert await m.acquire(category)
        pending=[asyncio.create_task(invoke(m,path,'POST')) for _ in range(88)]
        await asyncio.sleep(.03)
        assert not any(task.done() for task in pending)
        assert len(m.waiters[category])==88
        control=runtime.Admission(respond,settings('execution-control',processes=12,control_replicas=2,**profile))
        assert (await invoke(control,'/api/v1/worker/heartbeat','POST'))[0]['status']==200
        m.available[category]+=12
        responses=await asyncio.gather(*pending)
        assert all(response[0]['status']==200 for response in responses)
        assert m.available[category]==12 and not m.waiters[category]
        assert s.configured_connections==168
    asyncio.run(run())


def test_team_log_queue_remains_bounded_and_cancellation_frees_slots():
    from pathlib import Path
    profile=json.loads(Path('deploy/profiles/team-capacity-100.json').read_text())
    async def run():
        m=runtime.Admission(respond,settings('bulk-transfer',processes=12,control_replicas=2,**profile))
        for _ in range(12):assert await m.acquire('bulk_transfer')
        path='/api/v1/worker/executions/e/logs'
        pending=[asyncio.create_task(invoke(m,path,'POST')) for _ in range(96)]
        await asyncio.sleep(.03)
        assert len(m.waiters['bulk_transfer'])==96
        assert (await invoke(m,path,'POST'))[0]['status']==503
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        assert not m.waiters['bulk_transfer']
        m.available['bulk_transfer']+=12
        assert (await invoke(m,path,'POST'))[0]['status']==200
        assert m.available['bulk_transfer']==12
    asyncio.run(run())
