from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from backend.app import recovery_finish as finish
from backend.app.core import ExecutionRecovery, ExecutionRecoveryControl, Job, Run, Slot, RunLogState, Event, now
from backend.app.execution import Finish, RecoveryProof
from backend.tests.test_recovery_control import execution, apply as observe, observation
from backend.tests.test_execution_logs_postgres import pg, seed, api


@pytest.fixture(autouse=True)
def no_background_scheduler(monkeypatch):
    # Endpoint tests control cancellation explicitly; do not race a lifespan scheduler.
    monkeypatch.setenv('SCHEDULER_ENABLED', '0')


def prepare(factory,generation):
    with factory() as s:
        s.get(Job,'j').client_spec={'timeout_seconds':0,'template':{'task_type':'command'}}
        s.get(Run,'r').lease_expires_at=now()-timedelta(days=365)
        s.commit()
    observe(factory,generation,state='exited',exit_code=0)
    return RecoveryProof(generation=generation,identity=observation(generation).identity)


def complete(factory,proof,**changes):
    body=Finish(**{'status':'SUCCEEDED','exit_code':0,'recovery':proof,**changes})
    with factory() as s:
        result=finish.finish(s,worker_id='w',execution_id='e',body=body);s.commit();return result


def cleanup(factory,proof):
    with factory() as s:
        result=finish.cleanup(s,worker_id='w',execution_id='e',proof=proof);s.commit();return result


def test_finish_does_not_release_cleanup_is_separate_and_idempotent(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:
        lease=s.get(Run,'r').lease_expires_at
        s.get(Slot,'s').quarantined=True;s.commit()
    first=complete(factory,proof,log_gap=True)
    assert first=={'status':'SUCCEEDED','cleanup_confirmed':False,'duplicate':False}
    assert complete(factory,proof,log_gap=True)['duplicate']
    with factory() as s:
        assert not s.get(Run,'r').cleanup_confirmed
        assert s.get(Run,'r').lease_expires_at==lease
        assert s.get(Slot,'s').quarantined
        assert s.get(RunLogState,'r').truncated
    assert not cleanup(factory,proof)['duplicate']
    assert cleanup(factory,proof)['duplicate']
    with factory() as s:
        assert s.get(Run,'r').cleanup_confirmed
        assert not s.get(Slot,'s').quarantined
        assert s.scalar(select(func.count()).select_from(Event))==1
    assert complete(factory,proof,log_gap=True)['cleanup_confirmed']
    with pytest.raises(HTTPException):complete(factory,proof,status='FAILED',log_gap=True)


@pytest.mark.parametrize('state',['CANCEL_REQUESTED','CANCELLED','TIMED_OUT','SUCCEEDED','FAILED'])
def test_platform_authority_wins_late_success(execution,state):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:s.get(Run,'r').status=state;s.commit()
    expected='CANCELLED' if state=='CANCEL_REQUESTED' else state
    assert complete(factory,proof)['status']==expected
    with factory() as s:
        assert s.get(Run,'r').exit_code==0
        assert s.get(ExecutionRecoveryControl,'e').snapshot['completion']['status']=='SUCCEEDED'
        assert not s.get(Run,'r').cleanup_confirmed
    assert cleanup(factory,proof)['status']==expected


@pytest.mark.parametrize('reason',['execution_timeout','disconnection_timeout','revoked'])
def test_stop_decision_wins(execution,reason):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:
        if reason=='revoked':s.get(ExecutionRecovery,'e').revoked=True
        else:
            c=s.get(ExecutionRecoveryControl,'e');c.snapshot={**c.snapshot,'stop_reason':reason}
        s.commit()
    assert complete(factory,proof)['status']==('CANCELLED' if reason=='revoked' else 'TIMED_OUT')


@pytest.mark.parametrize('change',['generation','identity','missing_control','conflict','running','exit_code','missing_run','execution','slot_owner','unclaimed'])
def test_uncertain_or_foreign_completion_rejected(execution,change):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:
        c=s.get(ExecutionRecoveryControl,'e');r=s.get(Run,'r')
        if change=='generation':proof=proof.model_copy(update={'generation':'other'})
        elif change=='identity':proof=proof.model_copy(update={'identity':proof.identity.model_copy(update={'runtime_id':'b'*32})})
        elif change=='missing_control':s.delete(c)
        elif change=='conflict':c.snapshot={**c.snapshot,'identity_conflict':True}
        elif change=='running':c.snapshot={**c.snapshot,'observed_state':'running'}
        elif change=='exit_code':c.snapshot={**c.snapshot,'exit_code':1}
        elif change=='missing_run':s.delete(r)
        elif change=='execution':r.execution_id='other'
        elif change=='slot_owner':s.get(Slot,'s').worker_id='other'
        else:r.status='RESERVED'
        s.commit()
    with pytest.raises(HTTPException):complete(factory,proof)


def test_cleanup_requires_completion_and_keeps_other_quarantine(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with pytest.raises(HTTPException):cleanup(factory,proof)
    complete(factory,proof)
    with factory() as s:
        s.add(Run(id='r2',job_id='j',attempt=2,slot_id='s',execution_id='e2',status='TIMED_OUT',cleanup_confirmed=False,allocation_index=1))
        s.get(Slot,'s').quarantined=True;s.commit()
    cleanup(factory,proof)
    with factory() as s:assert s.get(Slot,'s').quarantined


def test_protocol_success_validates_results_before_lifecycle(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:s.get(Job,'j').client_spec={'template':{'task_type':'evaluation'}};s.commit()
    with pytest.raises(HTTPException):complete(factory,proof)
    assert complete(factory,proof,metrics={'episode_count':1,'success_rate':1,'runtime':1})['status']=='SUCCEEDED'
    with factory() as s:assert s.get(Run,'r').result_json['success_rate']==1


def test_foreign_worker_cannot_finish(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s,pytest.raises(HTTPException):
        finish.finish(s,worker_id='other',execution_id='e',body=Finish(status='SUCCEEDED',exit_code=0,recovery=proof))


def test_http_completion_cleanup_and_legacy_bypass_rejected(api):
    from backend.app.recovery_records import grant
    client,headers,factory=api
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=grant(s,run,'w').generation;s.commit()
    proof=prepare(factory,generation)
    base='/api/v1/worker/executions/e/'
    for suffix,body in [('complete',{'status':'SUCCEEDED','exit_code':0}),('cleanup',None)]:
        response=client.post(base+suffix,headers=headers,json=body)
        assert response.status_code==409,response.text
    body={'status':'SUCCEEDED','exit_code':0,'recovery':proof.model_dump()}
    response=client.post(base+'complete',headers=headers,json=body)
    assert response.status_code==200,response.text
    assert not response.json()['cleanup_confirmed']
    assert client.post(base+'complete',headers=headers,json=body).json()['duplicate']
    response=client.post(base+'cleanup',headers=headers,json=proof.model_dump())
    assert response.status_code==200,response.text
    assert response.json()['cleanup_confirmed']
    assert client.post(base+'cleanup',headers=headers,json=proof.model_dump()).json()['duplicate']


def test_postgres_cancel_lock_then_late_completion(pg):
    from backend.app.recovery_records import grant
    from backend.app.scheduling import lock
    factory=seed(pg[0])
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=grant(s,run,'w').generation;s.commit()
    proof=prepare(factory,generation)
    with factory() as cancellation:
        lock(cancellation)
        cancellation.get(Run,'r').status='CANCEL_REQUESTED'
        with pytest.raises(HTTPException) as error:complete(factory,proof)
        assert error.value.status_code==503
        cancellation.commit()
    assert complete(factory,proof)['status']=='CANCELLED'
    assert cleanup(factory,proof)['cleanup_confirmed']


def test_existing_log_gap_and_nonterminal_cleanup_rejected(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:s.add(RunLogState(run_id='r',truncated=False));s.commit()
    complete(factory,proof,log_gap=True)
    with factory() as s:s.get(Run,'r').status='RUNNING';s.commit()
    with pytest.raises(HTTPException):cleanup(factory,proof)


@pytest.mark.parametrize('state',['exited','missing'])
def test_repeated_exit_or_unloaded_runtime_can_finish(execution,state):
    factory,generation=execution;proof=prepare(factory,generation)
    reply=observe(factory,generation,sequence=2,state=state,exit_code=0 if state=='exited' else None)
    assert reply['action']=='retain'
    assert complete(factory,proof)['status']=='SUCCEEDED'
    assert cleanup(factory,proof)['cleanup_confirmed']


def test_changed_exit_code_requires_inspection(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    reply=observe(factory,generation,sequence=2,state='exited',exit_code=1)
    assert reply['action']=='inspect'
    with pytest.raises(HTTPException):complete(factory,proof)


def test_cancel_then_retry_old_reports_cannot_change_new_attempt(execution):
    from backend.app.retries import retry_job
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:
        s.get(Run,'r').status=s.get(Job,'j').status='CANCEL_REQUESTED';s.commit()
    with factory() as s,pytest.raises(HTTPException):retry_job(s,s.get(Job,'j'))
    assert complete(factory,proof)['status']=='CANCELLED'
    with factory() as s,pytest.raises(HTTPException):retry_job(s,s.get(Job,'j'))
    cleanup(factory,proof)
    with factory() as s:
        new=retry_job(s,s.get(Job,'j'));new_id=new.id;s.commit()
        assert new_id!='r' and new.attempt==2 and new.execution_id is None
    assert complete(factory,proof)['status']=='CANCELLED'
    assert cleanup(factory,proof)['duplicate']
    observe(factory,generation,sequence=2,state='exited',exit_code=0)
    with factory() as s:
        assert s.get(Job,'j').status=='PENDING'
        assert s.get(Run,new_id).status=='PENDING'
        assert s.get(Run,'r').status=='CANCELLED'


def sync_results(factory,proof):
    with factory() as s:
        result=finish.results_finished(s,worker_id='w',execution_id='e',proof=proof)
        s.commit();return result


def test_cleanup_releases_before_result_delivery_and_sync_requires_complete_data(execution):
    from backend.app.recovery_results import apply
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:
        job=s.get(Job,'j');job.episodes=1
        job.client_spec={'seed':100,'template':{'task_type':'evaluation','episode_reporting':True}}
        s.commit()
    metrics={'success_rate':1,'episode_count':1,'runtime':1}
    assert complete(factory,proof,metrics=metrics,pending_results=True)['status']=='SUCCEEDED'
    assert cleanup(factory,proof)['cleanup_confirmed']
    with factory() as s:
        assert s.get(ExecutionRecoveryControl,'e').snapshot['result_sync']=='pending'
        assert s.get(Run,'r').cleanup_confirmed
    with pytest.raises(HTTPException) as exc:sync_results(factory,proof)
    assert exc.value.status_code==400
    events=[{'episode_id':1,'seed':100,'status':'STARTED'},
        {'episode_id':1,'seed':100,'status':'COMPLETED','success':True,'metrics':{},'runtime':1,'artifacts':[]}]
    for sequence,body in enumerate(events):
        with factory() as s:
            apply(s,worker_id='w',execution_id='e',generation=generation,kind='episode',sequence=sequence,body=body);s.commit()
    assert sync_results(factory,proof)['result_sync']=='complete'
    assert sync_results(factory,proof)['duplicate']
    assert complete(factory,proof,metrics=metrics,pending_results=True)['duplicate']
    with factory() as s:
        assert s.get(Run,'r').status=='SUCCEEDED' and s.get(Run,'r').cleanup_confirmed


def test_pending_sync_does_not_reopen_cancelled_attempt_after_retry(execution):
    from backend.app.retries import retry_job
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:s.get(Run,'r').status=s.get(Job,'j').status='CANCEL_REQUESTED';s.commit()
    assert complete(factory,proof,pending_results=True)['status']=='CANCELLED'
    cleanup(factory,proof)
    with factory() as s:new_id=retry_job(s,s.get(Job,'j')).id;s.commit()
    assert sync_results(factory,proof)['status']=='CANCELLED'
    with factory() as s:
        assert s.get(Job,'j').status=='PENDING' and s.get(Run,new_id).status=='PENDING'


def test_result_sync_before_completion_is_rejected(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with pytest.raises(HTTPException) as exc:sync_results(factory,proof)
    assert exc.value.status_code==409


def test_pending_results_still_requires_valid_final_metrics(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    with factory() as s:s.get(Job,'j').client_spec={'template':{'task_type':'evaluation'}};s.commit()
    with pytest.raises(HTTPException):complete(factory,proof,pending_results=True)


def test_result_sync_before_cleanup_does_not_mark_complete(execution):
    factory,generation=execution;proof=prepare(factory,generation)
    complete(factory,proof,pending_results=True)
    with pytest.raises(HTTPException) as error:sync_results(factory,proof)
    assert error.value.status_code==409
    with factory() as s:
        assert s.get(ExecutionRecoveryControl,'e').snapshot['result_sync']=='pending'
        assert not s.get(Run,'r').cleanup_confirmed


def test_legacy_http_cannot_defer_required_results(api):
    client,headers,factory=api
    reply=client.post('/api/v1/worker/executions/e/complete',headers=headers,
        json={'status':'SUCCEEDED','exit_code':0,'pending_results':True})
    assert reply.status_code==400,reply.text


def test_result_sync_http_ack_and_replay(api):
    from backend.app.recovery_records import grant
    client,headers,factory=api
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=grant(s,run,'w').generation;s.commit()
    proof=prepare(factory,generation)
    complete(factory,proof,pending_results=True);cleanup(factory,proof)
    url='/api/v1/worker/executions/e/recovery/results-finished'
    for duplicate in (False,True):
        reply=client.post(url,headers=headers,json=proof.model_dump())
        assert reply.status_code==200,reply.text
        assert reply.json()=={'status':'SUCCEEDED','cleanup_confirmed':True,
            'result_sync':'complete','duplicate':duplicate}
