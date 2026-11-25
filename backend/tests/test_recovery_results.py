"""Late results preserve platform authority and commit with their ACK."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text

from backend.app import recovery_records, recovery_results
from backend.app.core import EvaluationBatch, EpisodeRecord, ExecutionRecovery, ExecutionRecoveryReceipt, Job, Run, Slot, now
from backend.tests.test_recovery_control import execution
from backend.tests.test_execution_logs_postgres import pg
from worker.recovery_delivery import RecoveryDelivery
from worker.reliable_outbox import ReliableOutbox, OutboxConflict


def setup(factory, task=False):
    with factory() as s:
        s.get(Job,'j').client_spec={'seed':100,'template':{
            'task_type':'command' if task else 'evaluation',
            'integration_mode':'sdk-task-result' if task else 'direct'}}
        s.get(Run,'r').lease_expires_at=now()-timedelta(days=365)
        s.commit()


def apply(factory,generation,body,**changes):
    with factory() as s:
        reply=recovery_results.apply(s,**{**dict(worker_id='w',execution_id='e',generation=generation,
            kind='episode',sequence=1,body=body),**changes})
        s.commit();return reply


@pytest.mark.parametrize('status',['RUNNING','CANCEL_REQUESTED','CANCELLED','TIMED_OUT','FAILED','SUCCEEDED'])
def test_late_episode_does_not_change_lifecycle_or_release(execution,status):
    factory,generation=execution;setup(factory)
    with factory() as s:
        run=s.get(Run,'r');run.status=status;s.commit();lease=run.lease_expires_at
    start={'episode_id':1,'seed':100,'status':'STARTED'}
    end={'episode_id':1,'seed':100,'status':'COMPLETED','success':True,'metrics':{},'runtime':1,'artifacts':[]}
    assert apply(factory,generation,start)['reconciled']
    assert apply(factory,generation,end,sequence=2)['reconciled']
    assert apply(factory,generation,end,sequence=2)['duplicate']
    with factory() as s:
        run=s.get(Run,'r')
        assert (run.status,run.lease_expires_at,run.cleanup_confirmed)==(status,lease,False)
        assert s.get(EpisodeRecord,('r',1)).result==end
        assert s.get(Job,'j').status=='RUNNING'


@pytest.mark.parametrize('kind',['task_result','episode'])
def test_receipt_only_then_apply_and_replay(execution,kind):
    factory,generation=execution;setup(factory,kind=='task_result')
    body={'success':False,'metrics':{'score':0}} if kind=='task_result' else {'episode_id':1,'seed':100,'status':'STARTED'}
    with factory() as s:
        recovery_records.receive(s,worker_id='w',execution_id='e',generation=generation,kind=kind,sequence=1,body=body);s.commit()
    assert apply(factory,generation,body,kind=kind)['duplicate']
    assert apply(factory,generation,body,kind=kind)['reconciled']
    with factory() as s:
        if kind=='task_result':assert s.get(Run,'r').result_json=={'kind':'task',**body}
        else:assert s.get(EpisodeRecord,('r',1))


@pytest.mark.parametrize('change',['worker','generation','revoked','deleted','execution','slot','slot_owner','missing_slot','missing_job'])
def test_foreign_revoked_deleted_or_changed_assignment_rejected(execution,change):
    factory,generation=execution;setup(factory)
    kwargs={}
    with factory() as s:
        if change=='worker':kwargs['worker_id']='other'
        elif change=='generation':generation='other'
        elif change=='revoked':s.get(ExecutionRecovery,'e').revoked=True
        elif change=='deleted':s.delete(s.get(Run,'r'))
        elif change=='execution':s.get(Run,'r').execution_id='other'
        elif change=='slot':s.get(Run,'r').slot_id='other'
        elif change=='slot_owner':s.get(Slot,'s').worker_id='other'
        elif change=='missing_slot':s.delete(s.get(Slot,'s'))
        else:s.get(Run,'r').job_id='missing'
        s.commit()
    with pytest.raises(HTTPException):apply(factory,generation,{},**kwargs)
    with factory() as s:assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt))==0


@pytest.mark.parametrize('kind,body,task',[
    ('episode',{'episode_id':1,'seed':100,'status':'COMPLETED','success':True,'metrics':{},'runtime':1,'artifacts':[]},False),
    ('episode',{'episode_id':1,'seed':999,'status':'STARTED'},False),
    ('task_result',{'success':True},False),
    ('task_result',{'success':'yes'},True),
    ('control',{},False),
])
def test_invalid_application_retains_rejected_receipt_or_rolls_back_conflict(execution,kind,body,task):
    factory,generation=execution;setup(factory,task)
    if (kind,body.get('status')) in (('episode','STARTED'),) or kind == 'task_result':
        reply=apply(factory,generation,body,kind=kind)
        assert reply['application']=='rejected' and reply['reconciled']
        assert apply(factory,generation,body,kind=kind)['application']=='rejected'
        expected=1
    else:
        with pytest.raises(HTTPException):apply(factory,generation,body,kind=kind)
        expected=0
    with factory() as s:
        grant=s.get(ExecutionRecovery,'e')
        assert grant.received_records==expected
        assert bool(grant.received_bytes)==bool(expected)
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt))==expected


def test_conflicting_task_result_never_overwrites(execution):
    factory,generation=execution;setup(factory,True)
    apply(factory,generation,{'success':True},kind='task_result')
    with pytest.raises(HTTPException):apply(factory,generation,{'success':False},kind='task_result',sequence=2)
    with factory() as s:assert s.get(Run,'r').result_json['success'] is True


def test_http_and_delivery_apply_before_ack(execution,tmp_path):
    factory,generation=execution;setup(factory,True)
    app=FastAPI()
    def db():
        with factory() as s:yield s
    recovery_records.install(app,db,lambda:SimpleNamespace(id='w'))
    q=ReliableOutbox(tmp_path/'outbox')
    q.put('e',generation,'task_result',1,{'success':True})
    with TestClient(app) as client:
        replies=[]
        def post(path,body):
            response=client.post('/api/v1/'+path,json=body)
            assert response.status_code==200,response.text
            replies.append(response.json());return response.json()
        sender=RecoveryDelivery(SimpleNamespace(post=post),q,apply_results=True)
        assert sender.step()==1 and replies[0]['reconciled']
        assert q.pending()==[]
    with factory() as s:assert s.get(Run,'r').result_json['success']


def test_deleted_batch_acknowledges_explicit_discard_without_resurrecting_result(execution,tmp_path):
    factory,generation=execution;setup(factory)
    with factory() as s:
        s.get(EvaluationBatch,'b').deleted_at=now()
        s.get(Run,'r').status='CANCEL_REQUESTED'
        s.commit()
    app=FastAPI()
    def db():
        with factory() as s:yield s
    recovery_records.install(app,db,lambda:SimpleNamespace(id='w'))
    queue=ReliableOutbox(tmp_path/'discarded')
    event={'episode_id':1,'seed':100,'status':'STARTED'}
    queue.put('e',generation,'episode',1,event)
    with TestClient(app) as client:
        def post(path,body):
            response=client.post('/api/v1/'+path,json=body)
            assert response.status_code==200,response.text
            return response.json()
        assert RecoveryDelivery(SimpleNamespace(post=post),queue,apply_results=True).step()==1
        assert queue.pending()==[]
        reply=post('worker/executions/e/recovery/results',{
            'generation':generation,'kind':'episode','sequence':1,'body':event,'apply':True})
        assert reply['duplicate'] and reply['application']=='discarded'
    with factory() as s:
        assert s.get(EpisodeRecord,('r',1)) is None
        assert s.get(Run,'r').status=='CANCEL_REQUESTED'
        assert s.get(ExecutionRecoveryReceipt,('e','episode',1)).application_state=='discarded'


def test_result_ack_lost_across_api_restart_reuses_original_receipt(execution, tmp_path):
    factory, generation = execution
    setup(factory, True)
    queue = ReliableOutbox(tmp_path/'outbox')
    queue.put('e', generation, 'task_result', 0, {'success': True})

    def app_instance():
        app = FastAPI()
        def db():
            with factory() as session:
                yield session
        recovery_records.install(app, db, lambda: SimpleNamespace(id='w'))
        return app

    with TestClient(app_instance()) as first_api:
        def lose_ack(path, body):
            response = first_api.post('/api/v1/'+path, json=body)
            assert response.status_code == 200, response.text
            raise TimeoutError('API committed result before response was lost')
        with pytest.raises(TimeoutError):
            RecoveryDelivery(SimpleNamespace(post=lose_ack), queue, apply_results=True).step()
    assert len(queue.pending()) == 1

    with TestClient(app_instance()) as restarted_api:
        def post(path, body):
            response = restarted_api.post('/api/v1/'+path, json=body)
            assert response.status_code == 200, response.text
            return response.json()
        reply = post('worker/executions/e/recovery/results', {
            'generation': generation, 'kind': 'task_result', 'sequence': 0,
            'body': {'success': True}, 'apply': True})
        assert reply['duplicate'] and reply['application'] == 'applied'
        assert RecoveryDelivery(SimpleNamespace(post=post), queue, apply_results=True).step() == 1
    assert queue.pending() == []
    with factory() as session:
        assert session.get(Run, 'r').result_json['success'] is True
        assert session.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt)) == 1


@pytest.mark.parametrize('reply_state', [False, True, 'missing_application'])
def test_delivery_keeps_unconfirmed_result_pending(tmp_path, reply_state):
    q=ReliableOutbox(tmp_path/'outbox')
    q.put('e','g','task_result',1,{'success':True})
    digest=q.pending()[0]['digest']
    response={'digest':digest,'reconciled':reply_state is not False}
    if reply_state is True:
        response['application']='unknown'
    sender=RecoveryDelivery(SimpleNamespace(post=lambda *a:response),q,apply_results=True)
    with pytest.raises(OutboxConflict):sender.step()
    assert len(q.pending())==1


@pytest.mark.parametrize('status',[401,409,503])
def test_delivery_keeps_result_on_http_failure(tmp_path,status):
    q=ReliableOutbox(tmp_path/'outbox')
    q.put('e','g','task_result',1,{'success':True})
    def post(*_):
        raise HTTPException(status,'request rejected')
    sender=RecoveryDelivery(SimpleNamespace(post=post),q,apply_results=True)
    with pytest.raises(HTTPException) as error:
        sender.step()
    assert error.value.status_code==status
    assert len(q.pending())==1


def test_invalid_result_is_durable_and_later_valid_result_still_arrives(execution,tmp_path):
    from backend.app import recovery_finish
    from backend.app.core import ExecutionRecoveryControl
    from backend.tests.test_recovery_finish import prepare, complete, cleanup
    factory,generation=execution
    setup(factory)
    proof=prepare(factory,generation)
    with factory() as session:
        job=session.get(Job,'j');job.episodes=1
        job.client_spec={'seed':100,'template':{'task_type':'evaluation','integration_mode':'direct'}}
        session.commit()
    assert complete(factory,proof,pending_results=True,
                    metrics={'success_rate':1,'episode_count':1,'runtime':1})['status']=='SUCCEEDED'
    assert cleanup(factory,proof)['cleanup_confirmed']
    q=ReliableOutbox(tmp_path/'outbox')
    invalid={'episode_id':1,'seed':999,'status':'STARTED'}
    start={'episode_id':1,'seed':100,'status':'STARTED'}
    end={'episode_id':1,'seed':100,'status':'COMPLETED','success':True,
         'metrics':{},'runtime':1,'artifacts':[]}
    for seq,body in enumerate((invalid,start,end)):
        q.put('e',generation,'episode',seq,body)
    app=FastAPI()
    def db():
        with factory() as session:yield session
    recovery_records.install(app,db,lambda:SimpleNamespace(id='w'))
    with TestClient(app) as client:
        replies=[];lost=False
        def post(path,body):
            nonlocal lost
            response=client.post('/api/v1/'+path,json=body)
            assert response.status_code==200,response.text
            if not lost:
                lost=True
                raise TimeoutError('receipt committed but ACK lost')
            replies.append(response.json())
            return response.json()
        sender=RecoveryDelivery(SimpleNamespace(post=post),q,apply_results=True)
        with pytest.raises(TimeoutError):sender.step()
        assert len(q.pending())==3
        assert sender.step()==3
        assert [x['application'] for x in replies]==['rejected','applied','applied']
        assert q.pending()==[]
    with factory() as session:
        state=recovery_finish.results_finished(session,worker_id='w',execution_id='e',proof=proof)
        session.commit()
        assert state['result_sync']=='failed'
        assert session.get(EpisodeRecord,('r',1)).result==end
        receipt=session.get(ExecutionRecoveryReceipt,('e','episode',0))
        assert receipt.payload==invalid and receipt.application_state=='rejected'
        assert receipt.rejection_reason == 'episode does not match execution plan'
        assert session.get(ExecutionRecoveryControl,'e').snapshot['result_sync']=='failed'
        assert session.get(Run,'r').status=='SUCCEEDED' and session.get(Run,'r').cleanup_confirmed


def test_missing_grant_rejected(execution):
    factory,generation=execution
    with pytest.raises(HTTPException):apply(factory,generation,{},execution_id='unknown')


def test_postgres_duplicate_application_without_global_lock(pg):
    from concurrent.futures import ThreadPoolExecutor
    from backend.tests.test_execution_logs_postgres import seed
    from backend.app.scheduling import lock
    factory=seed(pg[0]);setup(factory,True)
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(s,run,'w').generation;s.commit()
    def report(_):return apply(factory,generation,{'success':True},kind='task_result')
    with factory() as scheduler:
        lock(scheduler)
        with ThreadPoolExecutor(max_workers=4) as workers:
            replies=list(workers.map(report,range(8)))
    assert sum(not r['duplicate'] for r in replies)==1
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt))==1
        assert s.get(Run,'r').result_json['success'] is True
        assert not s.get(Run,'r').cleanup_confirmed


def test_postgres_cancel_then_late_result_preserves_cancel(pg):
    from concurrent.futures import ThreadPoolExecutor
    from backend.tests.test_execution_logs_postgres import seed
    import threading
    factory=seed(pg[0]);setup(factory,True)
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(s,run,'w').generation;s.commit()
    started=threading.Event()
    def report():
        started.set()
        return apply(factory,generation,{'success':True},kind='task_result')
    with ThreadPoolExecutor(max_workers=1) as executor:
        with factory() as cancelling:
            run=cancelling.scalar(select(Run).where(Run.id=='r').with_for_update())
            run.status='CANCEL_REQUESTED'
            future=executor.submit(report)
            assert started.wait(2)
            cancelling.commit()
        assert future.result(timeout=5)['reconciled']
    with factory() as s:
        run=s.get(Run,'r')
        assert run.status=='CANCEL_REQUESTED' and run.result_json['success']
        assert not run.cleanup_confirmed


def test_rejected_result_migration_and_downgrade_guard(pg,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from backend.tests.test_execution_logs_postgres import seed
    engine,url=pg
    monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'b03a7b8')
    command.upgrade(cfg,'c14d09e')
    command.downgrade(cfg,'b03a7b8')
    command.upgrade(cfg,'c14d09e')
    factory=seed(engine,create=False)
    with factory() as session:
        run=session.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(session,run,'w').generation
        session.commit()
    setup(factory)
    reply=apply(factory,generation,{'episode_id':1,'seed':999,'status':'STARTED'})
    assert reply['application']=='rejected'
    with pytest.raises(RuntimeError,match='rejected recovery results'):
        command.downgrade(cfg,'b03a7b8')
    with engine.connect() as connection:
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()=='c14d09e'


@pytest.mark.parametrize('state',['CANCELLED','TIMED_OUT','RUNNING'])
def test_real_worker_reader_outbox_http_replay_preserves_platform_state(execution,tmp_path,state):
    import json
    from worker.client import Worker
    factory,generation=execution;setup(factory)
    with factory() as s:
        s.get(Run,'r').status=state;s.commit()
    worker=object.__new__(Worker);worker.path=tmp_path/'worker.json'
    worker.state={'task':{'execution_id':'e','benchmark':'cs-client','log_protocol':2,
        'client_spec':{'template':{'integration_mode':'direct'}}},
        'recovery_assignment':{'claim':{'receipt':{'execution_id':'e','generation':generation}}}}
    root=tmp_path/'protocol';root.mkdir()
    event={'episode_id':1,'seed':100,'status':'STARTED'}
    (root/'events.jsonl').write_text(json.dumps(event)+'\n')
    q=ReliableOutbox(tmp_path/'outbox')
    assert worker.collect_recovery_results(q,protocol_root=root)
    app=FastAPI()
    def db():
        with factory() as s:yield s
    recovery_records.install(app,db,lambda:SimpleNamespace(id='w'))
    with TestClient(app) as client:
        replies=[]
        def post(path,body):
            response=client.post('/api/v1/'+path,json=body)
            assert response.status_code==200,response.text
            replies.append(response.json())
            if len(replies)==1:raise TimeoutError('applied but ACK lost')
            return response.json()
        delivery=RecoveryDelivery(SimpleNamespace(post=post),q,apply_results=True)
        with pytest.raises(TimeoutError):delivery.step()
        assert len(q.pending())==1
        worker.state=json.loads(worker.path.read_text())
        assert worker.collect_recovery_results(q,protocol_root=root)
        assert delivery.step()==1 and q.pending()==[]
        assert replies[-1]['duplicate'] and replies[-1]['reconciled']
    with factory() as s:
        stored=s.get(EpisodeRecord,('r',1)).started
        assert {k:stored[k] for k in event}==event
        assert stored['_platform_received_at']
        assert s.get(Run,'r').status==state
        assert not s.get(Run,'r').cleanup_confirmed
