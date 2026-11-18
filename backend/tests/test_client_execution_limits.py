from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node, submit, claim
from backend.tests.test_cs_protocol import template, submit_body
from backend.app.core import Run, Job, Slot, now
from backend.app.scheduling import execution_timeout, reconcile


def test_limits_auth_submission_and_preserved_snapshot(env, monkeypatch):
    main,c,auth=env
    monkeypatch.setenv('EXECUTION_TIMEOUT_SECONDS','7200')
    assert c.get('/api/v1/execution-limits').status_code==401
    limits=c.get('/api/v1/execution-limits',headers=auth['alice']).json()
    assert limits=={'client_execution_max_seconds':7200,'client_execution_default_seconds':300}
    body=submit_body(template(env));body['client']['timeout_seconds']=7200
    result=c.post('/api/v1/jobs',headers=auth['alice'],json=body)
    assert result.status_code==201,result.text
    with main.SessionLocal() as s:
        job=s.get(Job,result.json()['id'])
        assert job.client_spec['timeout_seconds']==7200
        monkeypatch.setenv('EXECUTION_TIMEOUT_SECONDS','60')
        assert execution_timeout(job)==7200
    assert c.get('/api/v1/execution-limits',headers=auth['alice']).json()['client_execution_default_seconds']==60
    bad=c.post('/api/v1/jobs',headers=auth['alice'],json=body)
    assert bad.status_code==400
    assert '7200' in bad.json()['detail'] and '60' in bad.json()['detail']
    for value in (-1,1.5):
        body['client']['timeout_seconds']=value
        assert c.post('/api/v1/jobs',headers=auth['alice'],json=body).status_code==400


@pytest.mark.parametrize('cause,expected',[('lease','执行心跳租约过期'),('startup','执行启动超时'),('execution','客户端执行总时限已到')])
def test_timeout_reasons_keep_cleanup_guard(env, monkeypatch, cause, expected):
    main,c,auth=env;token,slots=node(env)
    identity=submit(env);task=claim(env,token,slots[0]);at=now()
    monkeypatch.setenv('EXECUTION_TIMEOUT_SECONDS','10')
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==task['execution_id']))
        run.status='RUNNING';run.started_at=at;run.lease_expires_at=at+timedelta(seconds=60)
        if cause=='lease':run.lease_expires_at=at-timedelta(seconds=31)
        if cause=='startup':run.status='STARTING';run.enqueued_at=at-timedelta(seconds=121)
        if cause=='execution':run.started_at=at-timedelta(seconds=11)
        reconcile(s,at)
        assert run.status=='TIMED_OUT'
        assert expected in run.error
        assert not run.cleanup_confirmed
        assert s.get(Slot,run.slot_id).quarantined


def test_service_not_subject_to_client_limit(monkeypatch):
    monkeypatch.setenv('EXECUTION_TIMEOUT_SECONDS','1')
    assert execution_timeout(SimpleNamespace(benchmark='inference-service'))==31536000


def test_unlimited_snapshot_and_worker_protocol(env):
    from backend.app.scheduling import runtime_mismatch
    main,c,auth=env
    body=submit_body(template(env));body['client']['timeout_seconds']=0
    result=c.post('/api/v1/jobs',headers=auth['alice'],json=body)
    assert result.status_code==201,result.text
    with main.SessionLocal() as s:
        job=s.get(Job,result.json()['id'])
        assert execution_timeout(job)==0
        slot=SimpleNamespace(protocol_version=15)
        assert '协议 16' in runtime_mismatch(slot,job)


@pytest.mark.parametrize('cause',[None,'lease','startup'])
def test_unlimited_execution_keeps_lease_and_startup_limits(env,cause):
    main,c,auth=env;token,slots=node(env)
    submit(env);task=claim(env,token,slots[0]);at=now()
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==task['execution_id']))
        job=s.get(Job,run.job_id);job.client_spec={'timeout_seconds':0}
        run.status='RUNNING';run.started_at=at-timedelta(days=10);run.lease_expires_at=at+timedelta(seconds=60)
        if cause=='lease':run.lease_expires_at=at-timedelta(seconds=31)
        if cause=='startup':run.status='STARTING';run.enqueued_at=at-timedelta(seconds=121)
        reconcile(s,at)
        assert run.status==('TIMED_OUT' if cause else 'RUNNING')


def test_unlimited_claim_preserves_reservation_and_cancel(env):
    from backend.app.scheduling import tick
    main,c,auth=env;token,slots=node(env)
    body=submit_body(template(env));body['client']['timeout_seconds']=0
    body['resources']={'cpus':2,'ram_gb':8,'cpu_unlimited':True,'memory_unlimited':True}
    response=c.post('/api/v1/jobs',headers=auth['alice'],json=body)
    assert response.status_code==201,response.text
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=15;slot.client_images=['approved:cs']
        slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
        slot.runtime_report={'revision':0,'applied_revision':0};slot.runtime_reported_at=now();s.commit()
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).protocol_version=16;s.commit()
    assert tick(main.SessionLocal)==1
    # A downgrade between reservation and claim must not bypass the gate.
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).protocol_version=15;s.commit()
    url=f'/api/v1/worker/slots/{slots[0]}/claim'
    assert c.post(url,headers=token).json()['task'] is None
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).protocol_version=16;s.commit()
    task=c.post(url,headers=token).json()['task']
    assert task is not None
    assert (task['timeout_seconds'],task['cpus'],task['ram_gb'])==(0,0,0)
    assert (task['reserved_cpus'],task['reserved_ram_gb'])==(2,8)
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==task['execution_id']))
        assert (run.reserved_cpu,run.reserved_ram_gb)==(2,8)
    c.post('/api/v1/jobs/'+response.json()['id']+'/cancel',headers=auth['alice'])
    beat=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']})
    assert beat.json()['cancel'] is True


def test_unlimited_memory_minimum_still_blocks_insufficient_node(env):
    from backend.tests.test_resource_reporting import register,beat,samples
    from backend.app.scheduling import tick
    main,c,auth=env;token,slots=node(env)
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=16;slot.client_images=['approved:cs']
        slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
        slot.runtime_report={'revision':0,'applied_revision':0};slot.runtime_reported_at=now();s.commit()
    session=register(c,token);values=samples();values['host']['values']['memory_available_mb']=4096
    beat(c,token,session,samples=values)
    body=submit_body(template(env));body['resources']={'ram_gb':8,'memory_unlimited':True}
    response=c.post('/api/v1/jobs',headers=auth['alice'],json=body)
    assert response.status_code==201,response.text
    assert tick(main.SessionLocal)==0
    values['host']['values']['memory_available_mb']=16384
    beat(c,token,session,2,values)
    assert tick(main.SessionLocal)==1


def test_startup_renewal_preserves_startup_budget_and_does_not_start_clock(env):
    main,c,auth=env;token,slots=node(env)
    submit(env);task=claim(env,token,slots[0])
    for _ in range(3):
        response=c.post('/api/v1/worker/heartbeat',headers=token,json={
            'slot_id':slots[0],'execution_id':task['execution_id'],'started':False})
        assert response.status_code==200,response.text
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==task['execution_id']))
        assert run.status=='STARTING' and run.started_at is None
        at=now();assert run.lease_expires_at.replace(tzinfo=at.tzinfo)>at
        run.enqueued_at=at-timedelta(seconds=121)
        reconcile(s,at)
        assert run.status=='TIMED_OUT' and '执行启动超时' in run.error
