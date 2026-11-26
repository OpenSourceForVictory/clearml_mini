"""Legacy HTTP behavior stays valid while negotiated recovery is rolled out."""
import pytest
from sqlalchemy import select, func
from backend.tests.test_execution import env, node, submit, claim
from backend.app.core import ExecutionRecovery, Run, Job
from backend.app.priority_routes import role_for, accepts_role


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_old_worker_cancel_retry_late_completion_cannot_change_new_attempt(env, monkeypatch, enabled):
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED', enabled)
    main, client, auth = env
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    original = task['execution_id']
    beat = {'slot_id': slots[0], 'execution_id': original}
    assert client.post('/api/v1/worker/heartbeat', headers=token, json=beat).status_code == 200
    assert client.post(f'/api/v1/jobs/{job}/cancel', headers=auth['bob']).status_code == 404
    assert client.post(f'/api/v1/jobs/{job}/cancel', headers=auth['alice']).status_code == 202
    assert client.post('/api/v1/worker/heartbeat', headers=token, json=beat).json()['cancel']
    assert client.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).status_code == 409
    result = {'status': 'CANCELLED'}
    path = f'/api/v1/worker/executions/{original}/complete'
    assert client.post(path, headers=token, json=result).status_code == 200
    assert client.post(f'/api/v1/jobs/{job}/retry', headers=auth['alice']).status_code == 202
    new = claim(env, token, slots[0])
    assert new['execution_id'] != original
    assert client.post(path, headers=token, json=result).status_code == 200
    assert client.post(path, headers=token, json={'status':'SUCCEEDED', 'metrics':{
        'success_rate':1, 'episode_count':3, 'runtime':1}}).status_code == 409
    with main.SessionLocal() as s:
        runs = s.scalars(select(Run).where(Run.job_id == job).order_by(Run.attempt)).all()
        assert len(runs) == 2
        assert runs[0].status == 'CANCELLED' and runs[0].cleanup_confirmed
        assert runs[1].status == s.get(Job, job).status == 'STARTING'
        assert not runs[1].cleanup_confirmed
        assert s.scalar(select(func.count()).select_from(ExecutionRecovery)) == 0


@pytest.mark.parametrize('method,path,expected', [
    ('POST','/api/v1/worker/heartbeat','execution-control'),
    ('POST','/api/v1/worker/executions/e/complete','execution-control'),
    ('POST','/api/v1/worker/executions/e/cleanup','execution-control'),
    ('POST','/api/v1/jobs/j/cancel','interaction'),
    ('POST','/api/v1/jobs/j/retry','interaction'),
    ('POST','/api/v1/worker/slots/s/claim','worker-data'),
    ('POST','/api/v1/worker/executions/e/logs','bulk-transfer'),
])
def test_legacy_lifecycle_routes_have_exactly_one_processing_role(method,path,expected):
    assert role_for(path,method) == expected
    roles = ['interaction','execution-control','worker-data','bulk-transfer']
    assert [r for r in roles if accepts_role(r,path,method)] == [expected]


@pytest.mark.parametrize('occupied_role,occupied_category', [
    ('interaction', 'page_read'), ('worker-data', 'worker_claim'),
    ('worker-data', 'worker_result'), ('worker-data', 'worker_observation'),
    ('bulk-transfer', 'bulk_transfer'),
])
def test_busy_noncontrol_pool_preserves_control_and_management(pg, occupied_role, occupied_category):
    from sqlalchemy import text
    from sqlalchemy.exc import TimeoutError
    from backend.app import db_budget, priority_runtime
    from backend.tests.test_priority_runtime import settings
    pools = dict.fromkeys(priority_runtime.DEFAULT_POOLS, 1)
    factories = {role: db_budget.factories(pg[1], settings(role, pool_sizes=pools, pool_timeout=.02), isolated=True)
                 for role in priority_runtime.ROLE_POOLS}
    try:
        occupied_factory = factories[occupied_role][occupied_category]
        with occupied_factory() as occupied:
            assert occupied.scalar(text('SELECT 1')) == 1
            with pytest.raises(TimeoutError):
                with occupied_factory() as extra:
                    extra.execute(text('SELECT 1'))
            for role, category in [('execution-control', 'execution_control'), ('interaction', 'user_action')]:
                with factories[role][category]() as available:
                    assert available.scalar(text('SELECT 1')) == 1
            assert occupied_factory.kw['bind'].pool.checkedout() == 1
        with occupied_factory() as recovered:
            assert recovered.scalar(text('SELECT 1')) == 1
    finally:
        for categories in factories.values():
            for factory in categories.values():
                factory.kw['bind'].dispose()


from backend.tests.test_execution_logs_postgres import pg
