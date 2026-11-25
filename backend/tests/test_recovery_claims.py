import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.pool import StaticPool

from backend.app import recovery_claims as claims
from backend.app.core import ExecutionRecovery, ExecutionRecoveryClaim, Run, Slot, now
from backend.tests.test_execution_logs_postgres import seed, pg
from worker.recovery_claim import RecoveryClaim


@pytest.fixture
def assignment():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    factory=seed(engine)
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING';run.enqueued_at=now();s.commit()
    yield factory
    engine.dispose()


def arguments(**changes):
    return {'request_id':str(uuid.uuid4()),'worker_id':'w','slot_id':'s','run_id':'r',
            'task':{'execution_id':'e','run_id':'r','job_id':'j','command':['python','app.py'],'environment':{'X':'original'}},**changes}


def bind(factory,args):
    with factory() as s:
        response=claims.bind(s,**args);s.commit();return response


def lookup(factory,request_id,**changes):
    with factory() as s:return claims.lookup(s,**{'worker_id':'w','slot_id':'s','request_id':request_id,**changes})


def test_snapshot_replay_preserves_task_generation_and_original_lease(assignment):
    args=arguments();first=bind(assignment,args)
    with assignment() as s:
        run=s.get(Run,'r');run.lease_expires_at=now()-timedelta(days=365)
        run.status='RUNNING';deadline=run.lease_expires_at;s.commit()
    assert bind(assignment,args)==first
    assert lookup(assignment,args['request_id'])==first
    assert first['launch_authorized'] is False
    args['task']['environment']['X']='changed'
    with pytest.raises(HTTPException,match='different content'):bind(assignment,args)
    assert lookup(assignment,args['request_id'])['task']['environment']['X']=='original'
    with assignment() as s:
        assert s.get(Run,'r').lease_expires_at.replace(tzinfo=deadline.tzinfo)==deadline
        assert not s.get(Run,'r').cleanup_confirmed
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==1


@pytest.mark.parametrize('kind',['revoked','deleted','conflict','cancel','timeout','succeeded'])
def test_receipt_after_platform_stop_never_carries_launch_snapshot(assignment,kind):
    args=arguments();first=bind(assignment,args)
    with assignment() as s:
        run=s.get(Run,'r')
        if kind=='revoked':s.get(ExecutionRecovery,'e').revoked=True
        elif kind=='deleted':s.delete(run)
        elif kind=='conflict':run.execution_id='other'
        else:run.status={'cancel':'CANCEL_REQUESTED','timeout':'TIMED_OUT','succeeded':'SUCCEEDED'}[kind]
        s.commit()
    result=lookup(assignment,args['request_id'])
    assert result['disposition']==(kind if kind in ('revoked','deleted','conflict') else 'stop')
    assert result['task'] is None and result['launch_authorized'] is False
    assert result['generation']==first['generation']


@pytest.mark.parametrize('changes',[{'worker_id':'other'},{'slot_id':'other'},{'request_id':str(uuid.uuid4())}])
def test_foreign_or_missing_receipt_is_hidden(assignment,changes):
    args=arguments();bind(assignment,args)
    request=changes.pop('request_id',args['request_id'])
    with pytest.raises(HTTPException) as error:lookup(assignment,request,**changes)
    assert error.value.status_code==404


@pytest.mark.parametrize('changes',[{'worker_id':'other'},{'slot_id':'other'},{'run_id':'other'},
    {'task':{'execution_id':'wrong','run_id':'r','job_id':'j'}},
    {'task':{'execution_id':'e','run_id':'r','job_id':'wrong'}},
    {'task':None},{'task':{'bad':float('nan')}},{'task':{'bad':object()}},
    {'task':{'bad':'x'*(1024*1024)}},{'request_id':'bad'},{'request_id':None},{'request_id':'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'}])
def test_bad_binding_rolls_back_without_authority(assignment,changes):
    with pytest.raises(HTTPException):bind(assignment,arguments(**changes))
    with assignment() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==0
        assert s.scalar(select(func.count()).select_from(ExecutionRecovery))==0


def test_second_request_and_policy_change_cannot_rebind_execution(assignment):
    args=arguments();bind(assignment,args)
    with pytest.raises(HTTPException,match='another claim'):bind(assignment,{**args,'request_id':str(uuid.uuid4())})
    with pytest.raises(HTTPException,match='immutable'):bind(assignment,{**args,'tolerance':10})


def test_grant_and_claim_are_one_transaction(assignment):
    args=arguments()
    with assignment() as s:
        claims.bind(s,**args);s.rollback()
    with pytest.raises(HTTPException):lookup(assignment,args['request_id'])
    with assignment() as s:assert s.get(ExecutionRecovery,'e') is None


def test_http_lost_claim_response_recovers_without_launching(assignment,tmp_path):
    journal=RecoveryClaim(tmp_path/'claim')
    request=journal.begin(slot_id='s',known_execution_ids=[],requested_at=10,boot_id='boot')
    args=arguments(request_id=request['request_id'])
    first=bind(assignment,args)  # committed response lost; node has no task yet
    journal.close()
    journal=RecoveryClaim(tmp_path/'claim')
    recovered=journal.begin(slot_id='s',known_execution_ids=['other'],requested_at=1000,boot_id='boot')
    assert recovered==request
    def db():
        with assignment() as s:yield s
    app=FastAPI();claims.install(app,db,lambda:SimpleNamespace(id='w'))
    try:
        with TestClient(app) as client:
            result=client.post('/api/v1/worker/slots/s/recovery/claim',json={'request_id':request['request_id']})
            assert result.status_code==200 and result.json()==first
            journal.accept(result.json())
        saved=[];journal.handoff(saved.append)
        assert saved[0]['reconciliation_required'] is True
        assert saved[0]['claim']['requested_at']==10
        assert saved[0]['claim']['receipt']['execution_id']=='e'
    finally:journal.close()


def test_postgres_duplicate_claim_transaction_and_nonblocking_lookup(pg):
    from backend.app.scheduling import lock
    factory=seed(pg[0]);args=arguments()
    with factory() as s:s.get(Run,'r').status='STARTING';s.commit()
    def attempt(_):
        try:return bind(factory,args)
        except HTTPException as exc:
            assert exc.status_code==503;return None
    with ThreadPoolExecutor(max_workers=8) as pool:replies=list(pool.map(attempt,range(16)))
    successful=[r for r in replies if r is not None]
    assert successful and all(r==successful[0] for r in successful)
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryClaim))==1
        lock(s)
        assert lookup(factory,args['request_id'])==successful[0]


def test_postgres_claim_migration_guard(pg,monkeypatch):
    from alembic import command
    from alembic.config import Config
    engine,url=pg;monkeypatch.setenv('DATABASE_URL',url);cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'b03a7b8');command.downgrade(cfg,'af296a7');command.upgrade(cfg,'b03a7b8')
    factory=seed(engine,create=False)
    with factory() as s:s.get(Run,'r').status='STARTING';s.commit()
    bind(factory,arguments())
    with pytest.raises(RuntimeError,match='claim snapshots remain'):command.downgrade(cfg,'af296a7')
    with engine.connect() as c:assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='b03a7b8'


@pytest.mark.parametrize('state',['RESERVED','RUNNING','CANCEL_REQUESTED','TIMED_OUT'])
def test_unnegotiated_execution_cannot_gain_recovery_authority_later(assignment,state):
    with assignment() as s:s.get(Run,'r').status=state;s.commit()
    with pytest.raises(HTTPException,match='newly claimed'):bind(assignment,arguments())
    with assignment() as s:assert s.get(ExecutionRecovery,'e') is None


def test_real_http_worker_auth_and_slot_boundaries(pg,monkeypatch):
    # Exercise the actual installed router and worker_auth, not a stub identity.
    from backend.app.execution import token_hash
    from backend.app.core import Worker
    monkeypatch.setenv('JWT_SECRET','claim-local-test')
    from backend.app import main
    factory=seed(pg[0]);monkeypatch.setattr(main,'SessionLocal',factory)
    with factory() as s:
        s.get(Worker,'w').token_hash=token_hash('wk_claim_local_test')
        s.get(Run,'r').status='STARTING';s.commit()
    args=arguments();bind(factory,args)
    with TestClient(main.app) as client:
        path='/api/v1/worker/slots/s/recovery/claim';body={'request_id':args['request_id']}
        assert client.post(path,json=body).status_code==401
        headers={'Authorization':'Bearer wk_claim_local_test'}
        assert client.post(path,headers=headers,json=body).status_code==200
        assert client.post(path.replace('/s/','/other/'),headers=headers,json=body).status_code==404
        assert client.post(path,headers=headers,json={'request_id':'bad'}).status_code==400
