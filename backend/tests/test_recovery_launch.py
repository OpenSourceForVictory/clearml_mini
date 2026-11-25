import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.app import recovery_launch as launch, recovery_control as control
from backend.app.core import ExecutionRecovery, ExecutionRecoveryControl, Run, Slot, now
from backend.tests.test_recovery_claims import assignment, arguments, bind
from backend.tests.test_execution_logs_postgres import pg
from backend.tests.test_recovery_control import observation


def setup(factory):
    args=arguments();receipt=bind(factory,args)
    request=launch.LaunchRequest(generation=receipt['generation'],request_id=args['request_id'],
        launch_id=uuid.uuid4(),boot_id='boot-one',kind='host')
    return request


def prepare(factory,request,**changes):
    with factory() as s:
        reply=launch.prepare(s,**dict(worker_id='w',execution_id='e',request=request,**changes))
        s.commit();return reply


def test_launch_replay_does_not_renew_or_reset(assignment,monkeypatch):
    request=setup(assignment);at=now();monkeypatch.setattr(launch,'now',lambda:at)
    with assignment() as s:before=s.get(Run,'r').lease_expires_at
    assert prepare(assignment,request)['action']=='launch'
    assert prepare(assignment,request)['duplicate'] is True
    with assignment() as s:
        assert s.get(Run,'r').lease_expires_at==before
        assert s.get(Run,'r').started_at is None
    monkeypatch.setattr(launch,'now',lambda:at+timedelta(seconds=30))
    assert prepare(assignment,request)['reason']=='launch_confirmation_expired'
    monkeypatch.setattr(launch,'now',lambda:at)
    assert prepare(assignment,request)['action']=='inspect'


@pytest.mark.parametrize('state',['CANCEL_REQUESTED','CANCELLED','TIMED_OUT','SUCCEEDED','FAILED'])
def test_platform_decision_overrides_replayed_launch(assignment,state):
    request=setup(assignment);prepare(assignment,request)
    with assignment() as s:s.get(Run,'r').status=state;s.commit()
    assert prepare(assignment,request)['action']=='stop'
    with assignment() as s:
        assert not s.get(Run,'r').cleanup_confirmed
        s.get(Run,'r').status='STARTING';s.commit()
    assert prepare(assignment,request)['action']=='stop'


@pytest.mark.parametrize('change',['worker','generation','claim','slot','launch','boot','kind'])
def test_wrong_identity_never_gets_permission(assignment,change):
    request=setup(assignment);prepare(assignment,request)
    args={'worker_id':'w','execution_id':'e','request':request}
    if change=='worker':args['worker_id']='other'
    elif change=='slot':
        with assignment() as s:s.get(Slot,'s').worker_id='other';s.commit()
    else:
        field={'claim':'request_id','launch':'launch_id','boot':'boot_id','kind':'kind','generation':'generation'}[change]
        value=uuid.uuid4() if field.endswith('_id') and field!='boot_id' else 'docker' if field=='kind' else 'other'
        args['request']=request.model_copy(update={field:value})
    with assignment() as s,pytest.raises(HTTPException):launch.prepare(s,**args)


@pytest.mark.parametrize('state',['deleted','changed','running','revoked','conflict'])
def test_missing_or_existing_runtime_never_launches(assignment,state):
    request=setup(assignment)
    with assignment() as s:
        run=s.get(Run,'r')
        if state=='deleted':s.delete(run)
        elif state=='changed':run.execution_id='other'
        elif state=='running':run.status='RUNNING'
        elif state=='revoked':s.get(ExecutionRecovery,'e').revoked=True
        else:s.add(ExecutionRecoveryControl(execution_id='e',snapshot={'identity_conflict':True}))
        s.commit()
    assert prepare(assignment,request)['action'] in ('stop','inspect')


@pytest.mark.parametrize('origin',['missing','expired','fresh'])
def test_finite_tolerance_before_start(assignment,origin):
    args=arguments();receipt=bind(assignment,{**args,'tolerance':10})
    request=launch.LaunchRequest(generation=receipt['generation'],request_id=args['request_id'],
        launch_id=uuid.uuid4(),boot_id='boot-one',kind='host')
    with assignment() as s:
        s.get(Run,'r').enqueued_at=None if origin=='missing' else now()-timedelta(seconds=20 if origin=='expired' else 0)
        s.commit()
    assert prepare(assignment,request)['action']=={'missing':'inspect','expired':'stop','fresh':'launch'}[origin]


@pytest.mark.parametrize('kind,boot,expected',[('host','boot-one','track'),('docker','boot-one','inspect'),('host','boot-two','inspect')])
def test_first_process_identity_must_match_confirmed_boot_and_mode(assignment,kind,boot,expected):
    request=setup(assignment);prepare(assignment,request)
    with assignment() as s:
        result=control.reconcile(s,worker_id='w',execution_id='e',observation=observation(request.generation,
            identity={'kind':kind,'boot_id':boot,'runtime_id':'a'*(32 if kind=='host' else 64)}))
        s.commit()
    assert result['action']==expected
    assert prepare(assignment,request)['action']=='inspect'


def test_missing_execution_is_hidden(assignment):
    request=setup(assignment)
    with assignment() as s,pytest.raises(HTTPException) as e:
        launch.prepare(s,worker_id='w',execution_id='missing',request=request)
    assert e.value.status_code==404


def test_http_claim_to_launch_and_late_cancel(assignment,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from worker.recovery_launch import RecoveryLaunch
    args=arguments();receipt=bind(assignment,args)
    j=RecoveryLaunch(tmp_path/'launch',execution_id='e',generation=receipt['generation'],
        request_id=args['request_id'],boot_id='boot-one',kind='host')
    def db():
        with assignment() as s:yield s
    app=FastAPI();launch.install(app,db,lambda:SimpleNamespace(id='w'))
    try:
        with TestClient(app) as client:
            body=j.request(1);path='/api/v1/worker/executions/e/recovery/prepare'
            lost=client.post(path,json=body);assert lost.status_code==200
            with assignment() as s:s.get(Run,'r').status='CANCEL_REQUESTED';s.commit()
            response=client.post(path,json=body)
            assert response.status_code==200 and response.json()['duplicate']
            assert not j.accept(response.json(),2)
            with pytest.raises(RuntimeError):j.start(3,lambda _:pytest.fail('cancelled'))
    finally:j.close()


def test_postgres_launch_cancel_lock_and_duplicate_serialization(pg):
    from backend.tests.test_execution_logs_postgres import seed
    from backend.app.scheduling import lock
    from concurrent.futures import ThreadPoolExecutor
    factory=seed(pg[0])
    with factory() as s:s.get(Run,'r').status='STARTING';s.get(Run,'r').enqueued_at=now();s.commit()
    request=setup(factory)
    def attempt(_):
        try:return prepare(factory,request)
        except HTTPException as exc:
            assert exc.status_code==503;return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(attempt,range(8)))
    assert any(r and r['action']=='launch' for r in results)
    with factory() as s:
        lock(s);s.get(Run,'r').status='CANCEL_REQUESTED';s.flush()
        assert attempt(None) is None
        s.commit()
    assert prepare(factory,request)['reason']=='cancelled'


def test_short_offline_tolerance_bounds_launch_window_and_late_ack(assignment,tmp_path,monkeypatch):
    from worker.recovery_launch import RecoveryLaunch
    args=arguments();receipt=bind(assignment,{**args,'tolerance':10});at=now()
    with assignment() as s:s.get(Run,'r').enqueued_at=at-timedelta(seconds=9.5);s.commit()
    monkeypatch.setattr(launch,'now',lambda:at)
    j=RecoveryLaunch(tmp_path/'launch',execution_id='e',generation=receipt['generation'],
        request_id=args['request_id'],boot_id='boot-one',kind='host')
    try:
        request=launch.LaunchRequest(**j.request(100))
        response=prepare(assignment,request)
        assert response['action']=='launch' and response['valid_for_seconds']==.5
        monkeypatch.setattr(launch,'now',lambda:at+timedelta(seconds=.1))
        replay=prepare(assignment,request)
        assert replay['duplicate'] and replay['valid_for_seconds']==.5
        assert not j.accept(replay,100.5)  # exact local cutoff, including HTTP delay
        with pytest.raises(RuntimeError):j.start(100.5,lambda _:pytest.fail('offline cutoff passed'))
        monkeypatch.setattr(launch,'now',lambda:at+timedelta(seconds=.5))
        assert prepare(assignment,request)['reason']=='disconnection_timeout'
    finally:j.close()


def test_expired_launch_window_does_not_stop_already_started_program(assignment,monkeypatch):
    from backend.app.core import Job
    request=setup(assignment);at=now()
    with assignment() as s:s.get(Job,'j').client_spec={'timeout_seconds':0};s.commit()
    monkeypatch.setattr(launch,'now',lambda:at)
    assert prepare(assignment,request)['action']=='launch'
    later=at+timedelta(seconds=31)
    monkeypatch.setattr(launch,'now',lambda:later)
    assert prepare(assignment,request)['reason']=='launch_confirmation_expired'
    monkeypatch.setattr(control,'now',lambda:later)
    with assignment() as s:
        result=control.reconcile(s,worker_id='w',execution_id='e',observation=observation(request.generation,elapsed_seconds=31))
        s.commit()
        assert result['action']=='track'
        assert control.utc(s.get(Run,'r').started_at)==at
