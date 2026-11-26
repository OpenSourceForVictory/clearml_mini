import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, func
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Run, Slot, ExecutionRecovery, ExecutionRecoveryClaim, now
from backend.app.scheduling import tick


def enabled(env,monkeypatch):
    main,c,_=env;token,slots=node(env)
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED','1')
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).software={'capabilities':['disconnected_execution_v1']};s.commit()
    return token,slots[0]


def request(**changes):return {'known_execution_ids':[],'request_id':str(uuid.uuid4()),'recovery_version':1,**changes}


def test_claim_commit_response_replay_preserves_original_snapshot(env,monkeypatch):
    main,c,_=env;token,slot=enabled(env,monkeypatch);job=submit(env);tick(main.SessionLocal)
    path=f'/api/v1/worker/slots/{slot}/claim';body=request()
    first=c.post(path,headers=token,json=body)
    assert first.status_code==200,first.text
    first=first.json();task=first['task'];receipt=first['recovery_claim']
    assert receipt['task']==task and receipt['launch_authorized'] is False
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.job_id==job));start=run.enqueued_at
        run.lease_expires_at=now()-timedelta(days=365);lease=run.lease_expires_at;s.commit()
    # Reconnect can find the original receipt even after new grants are disabled.
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED','0')
    assert c.post(path,headers=token,json=body).json()==first
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.job_id==job))
        assert run.enqueued_at==start and run.lease_expires_at.replace(tzinfo=lease.tzinfo)==lease
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==1
        assert s.scalar(select(func.count()).select_from(ExecutionRecovery))==1
        run.status='CANCEL_REQUESTED';s.commit()
    late=c.post(path,headers=token,json=body).json()
    assert late['task'] is None and late['recovery_claim']['disposition']=='stop'
    assert late['recovery_claim']['generation']==receipt['generation']


def test_new_request_cannot_adopt_previous_claim(env,monkeypatch):
    main,c,_=env;token,slot=enabled(env,monkeypatch);submit(env);tick(main.SessionLocal)
    path=f'/api/v1/worker/slots/{slot}/claim'
    assert c.post(path,headers=token,json=request()).status_code==200
    assert c.post(path,headers=token,json=request()).status_code==409


@pytest.mark.parametrize('fault',['disabled','capability','version_only','id_only'])
def test_incomplete_negotiation_never_claims(env,monkeypatch,fault):
    main,c,_=env;token,slot=enabled(env,monkeypatch);submit(env);tick(main.SessionLocal);body=request()
    if fault=='disabled':monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED','0')
    elif fault=='capability':
        with main.SessionLocal() as s:s.get(Slot,slot).software={};s.commit()
    elif fault=='version_only':body.pop('request_id')
    else:body.pop('recovery_version')
    response=c.post(f'/api/v1/worker/slots/{slot}/claim',headers=token,json=body)
    assert response.status_code==(400 if fault in ('version_only','id_only') else 409)
    with main.SessionLocal() as s:
        assert s.scalar(select(Run)).status=='RESERVED'
        assert s.scalar(select(func.count()).select_from(ExecutionRecovery))==0


def test_idle_negotiated_poll_has_explicit_empty_ack(env,monkeypatch):
    _,c,_=env;token,slot=enabled(env,monkeypatch);body=request()
    response=c.post(f'/api/v1/worker/slots/{slot}/claim',headers=token,json=body)
    assert response.status_code==200
    assert response.json()=={'request_id':body['request_id'],'task':None,'cleanup':[],'recovery_claim':None}


def test_legacy_poll_cannot_reclaim_or_cleanup_recovery_execution(env,monkeypatch):
    main,c,_=env;token,slot=enabled(env,monkeypatch);submit(env);tick(main.SessionLocal)
    path=f'/api/v1/worker/slots/{slot}/claim';body=request()
    assert c.post(path,headers=token,json=body).status_code==200
    assert c.post(path,headers=token).status_code==409
    with main.SessionLocal() as s:s.scalar(select(Run)).status='TIMED_OUT';s.commit()
    assert c.post(path,headers=token).status_code==409
    assert c.post(path,headers=token,json=request()).status_code==409
    assert c.post(path,headers=token,json=body).json()['recovery_claim']['disposition']=='stop'


def test_binding_failure_rolls_back_claim_transition(env,monkeypatch):
    from backend.app import recovery_claims
    main,c,_=env;token,slot=enabled(env,monkeypatch);submit(env);tick(main.SessionLocal)
    real=recovery_claims.bind
    def fail(*args,**kwargs):
        real(*args,**kwargs)
        raise RuntimeError('injected commit boundary failure')
    monkeypatch.setattr(recovery_claims,'bind',fail)
    with pytest.raises(RuntimeError):
        c.post(f'/api/v1/worker/slots/{slot}/claim',headers=token,json=request())
    with main.SessionLocal() as s:
        assert s.scalar(select(Run)).status=='RESERVED'
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==0
        assert s.scalar(select(func.count()).select_from(ExecutionRecovery))==0


def test_old_claim_after_cancel_and_retry_cannot_claim_new_attempt(env,monkeypatch):
    from backend.app.core import Job
    from backend.app.retries import retry_job
    main,c,_=env;token,slot=enabled(env,monkeypatch);job_id=submit(env);tick(main.SessionLocal)
    path=f'/api/v1/worker/slots/{slot}/claim';old_request=request()
    first=c.post(path,headers=token,json=old_request).json()
    old_execution=first['task']['execution_id']
    # Completion/cleanup is covered separately; here seed its committed state
    # to exercise the claim endpoint across a subsequent scheduling attempt.
    with main.SessionLocal() as s:
        job=s.get(Job,job_id);old=s.scalar(select(Run).where(Run.job_id==job_id))
        job.status=old.status='CANCELLED';old.cleanup_confirmed=True
        new=retry_job(s,job);new_id=new.id;s.commit()
    tick(main.SessionLocal)
    late=c.post(path,headers=token,json=old_request)
    assert late.status_code==200,late.text
    assert late.json()['task'] is None
    assert late.json()['recovery_claim']['execution_id']==old_execution
    assert late.json()['recovery_claim']['disposition']=='stop'
    with main.SessionLocal() as s:
        assert s.get(Run,new_id).status=='RESERVED'
    fresh=c.post(path,headers=token,json=request())
    assert fresh.status_code==200,fresh.text
    assert fresh.json()['task']['run_id']==new_id
    assert fresh.json()['task']['execution_id']!=old_execution


def test_worker_poll_recovers_lost_api_response_into_original_journal(env,monkeypatch,tmp_path):
    import json
    from types import SimpleNamespace
    from worker.recovery_poll import poll,CAPABILITY
    from worker.client import Worker
    main,c,_=env;token,slot=enabled(env,monkeypatch);submit(env);tick(main.SessionLocal)
    worker=SimpleNamespace(path=tmp_path/'worker.json',slot_id=slot,children={},
        config={'node_outbox_root':str(tmp_path/'shared-outbox')})
    def child(execution):
        path=tmp_path/(execution+'.json')
        return SimpleNamespace(path=path,state=json.loads(path.read_text()) if path.exists() else None,
            config={},runtime_snapshot={'revision':1},storage_snapshot={'revision':1})
    worker.child=child
    replies=[]
    def post(path,body):
        response=c.post('/api/v1/'+path,headers=token,json=body)
        assert response.status_code==200,response.text
        replies.append(response.json())
        if len(replies)==1:raise TimeoutError('API committed but response was lost')
        return response.json()
    worker.api=SimpleNamespace(post=post)
    with pytest.raises(TimeoutError):poll(worker,{'capabilities':[CAPABILITY]})
    assert worker.children=={}
    # Pending receipt must recover even if the release no longer advertises it.
    recovered=poll(worker,{'capabilities':[]})
    assert replies[0]==recovered
    execution=recovered['task']['execution_id'];saved=worker.children[execution]
    assert saved.state['task']==recovered['task']
    assert saved.state['recovery_assignment']['claim']['receipt']==recovered['recovery_claim']
    dispatched=[]
    monkeypatch.setattr('worker.recovery_execution.execute',lambda child:dispatched.append(child))
    Worker.execute(saved,saved.state['task'])
    assert dispatched==[saved]
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==1


@pytest.mark.parametrize('flag,expected', [('0', False), ('1', True)])
def test_registration_and_heartbeat_advertise_server_negotiation(env, monkeypatch, flag, expected):
    _, client, _ = env
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED', flag)
    token, slots = node(env)
    registered = client.post('/api/v1/worker/register', headers=token, json={
        'pool_id': 'mock', 'gpu_index': 0, 'gpu_uuid': 'mock:node:0',
        'gpu_model': 'virtual', 'vram_gb': 0})
    assert registered.status_code == 200
    assert registered.json()['recovery_execution_enabled'] is expected
    heartbeat = client.post('/api/v1/worker/heartbeat', headers=token,
                            json={'slot_id': slots[0]})
    assert heartbeat.status_code == 200
    assert heartbeat.json()['recovery_execution_enabled'] is expected
    # An old worker still receives the old execution shape even when the server opts in.
    submit(env)
    task = claim(env, token, slots[0])
    assert task is not None and 'recovery_claim' not in task
