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
    for value in (0,-1,1.5):
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
