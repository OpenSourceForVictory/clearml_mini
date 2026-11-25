import uuid

import pytest
from fastapi import HTTPException

from backend.app import recovery_unstarted as pending
from backend.app.core import ExecutionRecoveryControl, Run, Slot
from backend.tests.test_recovery_claims import assignment, arguments, bind
from backend.tests.test_recovery_launch import setup, prepare


def call(factory,body,**changes):
    with factory() as s:
        value=pending.settle(s,worker_id='w',execution_id='e',body=body,**changes)
        s.commit();return value


def cancelled(factory,with_launch):
    if with_launch:
        request=setup(factory)
        body=pending.NotStarted(generation=request.generation,request_id=request.request_id,launch=request)
    else:
        args=arguments();receipt=bind(factory,args)
        body=pending.NotStarted(generation=receipt['generation'],request_id=args['request_id'])
    with factory() as s:s.get(Run,'r').status='CANCEL_REQUESTED';s.commit()
    if with_launch:assert prepare(factory,request)['action']=='stop'
    return body


@pytest.mark.parametrize('with_launch',[False,True])
def test_cancel_before_start_releases_once_without_inventing_exit(assignment,with_launch):
    body=cancelled(assignment,with_launch)
    assert call(assignment,body)=={'execution_id':'e','generation':body.generation,
        'status':'CANCELLED','cleanup_confirmed':True,'duplicate':False}
    assert call(assignment,body)['duplicate']
    with assignment() as s:
        run=s.get(Run,'r');assert run.exit_code is None and run.started_at is None and run.cleanup_confirmed
        snapshot=s.get(ExecutionRecoveryControl,'e').snapshot
        assert snapshot['result_sync']=='complete' and 'identity' not in snapshot


@pytest.mark.parametrize('fault',['generation','claim','slot','launch','identity','conflict','running','authorized'])
def test_uncertain_or_wrong_assignment_never_releases(assignment,fault):
    body=cancelled(assignment,True)
    with assignment() as s:
        record=s.get(ExecutionRecoveryControl,'e')
        if fault=='generation':body=body.model_copy(update={'generation':'wrong'})
        elif fault=='claim':body=body.model_copy(update={'request_id':uuid.uuid4()})
        elif fault=='slot':s.get(Slot,'s').worker_id='other'
        elif fault=='launch':body=body.model_copy(update={'launch':None})
        elif fault=='identity':record.snapshot={**record.snapshot,'identity':{'runtime_id':'x'}}
        elif fault=='conflict':record.snapshot={**record.snapshot,'identity_conflict':True}
        elif fault=='running':s.get(Run,'r').status='RUNNING'
        else:
            s.get(Run,'r').status='STARTING';record.snapshot={}
            body=body.model_copy(update={'launch':None})
        s.commit()
    with pytest.raises(HTTPException):call(assignment,body)
    with assignment() as s:assert not s.get(Run,'r').cleanup_confirmed


def test_terminal_timeout_wins_and_conflicting_replay_is_rejected(assignment):
    body=cancelled(assignment,True)
    with assignment() as s:s.get(Run,'r').status='TIMED_OUT';s.commit()
    assert call(assignment,body)['status']=='TIMED_OUT'
    with pytest.raises(HTTPException):call(assignment,body.model_copy(update={'launch':None}))


@pytest.mark.parametrize('state',['CANCELLED','FAILED','SUCCEEDED','TIMED_OUT'])
def test_platform_terminal_is_preserved_without_launch(assignment,state):
    body=cancelled(assignment,False)
    with assignment() as s:s.get(Run,'r').status=state;s.commit()
    assert call(assignment,body)['status']==state


@pytest.mark.parametrize('fault',['missing','wrong_worker','missing_run','changed_execution','unconfirmed_launch'])
def test_missing_authority_or_unconfirmed_launch_fails_closed(assignment,fault):
    body=cancelled(assignment,False)
    if fault=='unconfirmed_launch':
        from backend.app.recovery_launch import LaunchRequest
        body=body.model_copy(update={'launch':LaunchRequest(generation=body.generation,
            request_id=body.request_id,launch_id=uuid.uuid4(),boot_id='boot',kind='host')})
    if fault in ('missing_run','changed_execution'):
        with assignment() as s:
            run=s.get(Run,'r')
            if fault=='missing_run':s.delete(run)
            else:run.execution_id='different'
            s.commit()
    with assignment() as s,pytest.raises(HTTPException):
        pending.settle(s,worker_id='wrong' if fault=='wrong_worker' else 'w',
                       execution_id='missing' if fault=='missing' else 'e',body=body)


def test_persisted_offline_cutoff_before_launch_is_timeout(assignment):
    body=cancelled(assignment,True)
    with assignment() as s:
        s.get(Run,'r').status='STARTING'
        control=s.get(ExecutionRecoveryControl,'e')
        control.snapshot={**control.snapshot,'stop_requested':True,'stop_reason':'disconnection_timeout'}
        s.commit()
    assert call(assignment,body)['status']=='TIMED_OUT'
