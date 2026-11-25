from datetime import timedelta
import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool

from backend.app import recovery_control as control, recovery_records
from backend.app.core import ExecutionRecovery, ExecutionRecoveryControl, Job, Run, ServiceInstance, now
from backend.tests.test_execution_logs_postgres import seed, pg


@pytest.fixture
def execution():
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    factory = seed(engine)
    with factory() as s:
        run = s.get(Run, 'r'); run.status = 'STARTING'; run.enqueued_at = now()
        grant = recovery_records.grant(s, run, 'w')
        generation = grant.generation; s.commit()
    yield factory, generation
    engine.dispose()


def observation(generation, **changes):
    return control.Observation(**{'generation':generation,'sequence':1,
        'identity':{'kind':'host','boot_id':'boot-one','runtime_id':'a'*32},
        'state':'running','elapsed_seconds':10, **changes})


def apply(factory, generation, **changes):
    with factory() as s:
        result = control.reconcile(s, worker_id='w', execution_id='e',
                                   observation=observation(generation, **changes))
        s.commit()
        return result


def test_track_expired_original_execution_and_replay_without_renewal(execution, monkeypatch):
    factory, generation = execution
    at = now()
    with factory() as s:
        s.get(Run, 'r').lease_expires_at = at - timedelta(days=365);s.commit()
    monkeypatch.setattr(control, 'now', lambda: at)
    assert apply(factory,generation)['action']=='track'
    with factory() as s:
        run=s.get(Run,'r');deadline=run.lease_expires_at;started=run.started_at
        assert run.status=='RUNNING' and not run.cleanup_confirmed
        assert control.utc(started)==at-timedelta(seconds=10)
    monkeypatch.setattr(control, 'now', lambda: at+timedelta(days=365))
    assert apply(factory,generation)['duplicate']
    with factory() as s:
        assert s.get(Run,'r').lease_expires_at==deadline
    with pytest.raises(HTTPException):
        apply(factory,generation,elapsed_seconds=11)
    assert apply(factory,generation,sequence=2,elapsed_seconds=365*86400+10)['action']=='stop'
    with factory() as s:assert s.get(Run,'r').started_at==started


@pytest.mark.parametrize('state',['CANCEL_REQUESTED','CANCELLED','FAILED','TIMED_OUT','SUCCEEDED'])
def test_platform_stop_is_sticky_and_never_releases_resources(execution,state):
    factory,generation=execution
    apply(factory,generation)
    with factory() as s:s.get(Run,'r').status=state;s.commit()
    assert apply(factory,generation,sequence=2)['action']=='stop'
    with factory() as s:s.get(Run,'r').status='RUNNING';s.commit()
    assert apply(factory,generation,sequence=3)['action']=='stop'
    with factory() as s:assert not s.get(Run,'r').cleanup_confirmed


@pytest.mark.parametrize('mode',['generation','worker','execution'])
def test_foreign_assignment_is_rejected(execution,mode):
    factory,generation=execution
    args={'worker_id':'w','execution_id':'e','observation':observation(generation)}
    if mode=='generation':args['observation']=observation('wrong')
    else:args[mode+'_id']='wrong'
    with factory() as s,pytest.raises(HTTPException):
        control.reconcile(s,**args)
    with factory() as s:assert s.get(ExecutionRecoveryControl,'e') is None


@pytest.mark.parametrize('change',[
    {'boot_id':'boot-two','runtime_id':'a'*32,'kind':'host'},
    {'boot_id':'boot-one','runtime_id':'b'*32,'kind':'host'},
    {'boot_id':'boot-one','runtime_id':'b'*64,'kind':'docker'},
])
def test_identity_conflict_requires_inspection_and_cannot_resume(execution,change):
    factory,generation=execution;apply(factory,generation)
    assert apply(factory,generation,sequence=2,identity=change)['action']=='inspect'
    assert apply(factory,generation,sequence=3)['action']=='inspect'
    with factory() as s:
        snapshot=s.get(ExecutionRecoveryControl,'e').snapshot
        assert snapshot['identity']['runtime_id']=='a'*32


@pytest.mark.parametrize('state',['exited','missing'])
def test_completion_evidence_is_not_cleanup_or_permission_to_restart(execution,state):
    factory,generation=execution;apply(factory,generation)
    reply=apply(factory,generation,sequence=2,state=state,exit_code=0 if state=='exited' else None)
    assert reply['action']=='retain' and not reply['cleanup_confirmed']
    assert apply(factory,generation,sequence=3)['action']=='inspect'
    with factory() as s:assert s.get(Run,'r').status=='RUNNING'


@pytest.mark.parametrize('kind',['revoked','deleted','other_slot','unclaimed','unbound_running','backwards'])
def test_recovery_safety_boundaries(execution,kind):
    factory,generation=execution
    if kind=='backwards':apply(factory,generation)
    with factory() as s:
        run=s.get(Run,'r')
        if kind=='revoked':s.get(ExecutionRecovery,'e').revoked=True
        elif kind=='deleted':s.delete(run)
        elif kind=='other_slot':run.slot_id='other'
        elif kind=='unclaimed':run.status='RESERVED'
        elif kind=='unbound_running':run.status='RUNNING'
        s.commit()
    reply=apply(factory,generation,sequence=2,elapsed_seconds=1)
    assert reply['action']==('stop' if kind=='revoked' else 'inspect')


def test_finite_tolerance_and_local_stop_never_renew(execution,monkeypatch):
    factory,generation=execution;at=now()
    with factory() as s:
        grant=s.get(ExecutionRecovery,'e')
        grant.policy={'version':1,'disconnect_tolerance_seconds':1}
        s.get(Run,'r').enqueued_at=at-timedelta(seconds=1);s.commit()
    monkeypatch.setattr(control,'now',lambda:at)
    result=apply(factory,generation)
    assert result['action']=='stop' and result['reason']=='disconnection_timeout'
    assert apply(factory,generation,sequence=2,stop_reason='cancelled')['reason']=='disconnection_timeout'


def test_local_stop_and_service_reprobe(execution):
    factory,generation=execution
    with factory() as s:
        run=s.get(Run,'r');run.lease_expires_at=now()-timedelta(days=1)
        job=s.get(Job,'j');job.benchmark='inference-service';job.service_instance_id='svc'
        s.add(ServiceInstance(id='svc',name='svc',worker_id='w',slot_id='s',address='127.0.0.1',
            port=19000,current_job_id='j',template_snapshot={},health_ready=True,health_at=now(),
            probe_state={'epoch':'old','sequence':20,'ever_ready':True}))
        s.commit()
    assert apply(factory,generation)['action']=='track'
    with factory() as s:
        item=s.get(ServiceInstance,'svc')
        assert not item.health_ready and item.health_at is None
        assert item.probe_state['epoch'] is None and item.probe_state['ever_ready']
    assert apply(factory,generation,sequence=2,stop_reason='node_restarted')['action']=='stop'


@pytest.mark.parametrize('changes',[
    {'sequence':True},{'sequence':0},{'state':'running','exit_code':0},
    {'elapsed_seconds':None},{'elapsed_seconds':float('inf')},
    {'identity':{'kind':'host','boot_id':'boot','runtime_id':'1234'}},
    {'identity':{'kind':'docker','boot_id':'boot','runtime_id':'a'*32}},
])
def test_invalid_observation(changes):
    with pytest.raises(ValidationError):observation('generation',**changes)


def test_http_commits_before_reply(execution):
    from types import SimpleNamespace
    factory,generation=execution
    def db():
        with factory() as s:yield s
    app=FastAPI();control.install(app,db,lambda:SimpleNamespace(id='w'))
    with TestClient(app) as client:
        response=client.post('/api/v1/worker/executions/e/recovery/reconcile',
            json=observation(generation).model_dump())
        assert response.status_code==200 and response.json()['action']=='track'
    with factory() as s:assert s.get(ExecutionRecoveryControl,'e').snapshot['reply']==response.json()


def test_lost_control_ack_after_api_restart_observes_platform_cancel(execution):
    from types import SimpleNamespace
    factory, generation = execution
    initial = observation(generation).model_dump()

    def app_instance():
        def db():
            with factory() as session:
                yield session
        app = FastAPI()
        control.install(app, db, lambda: SimpleNamespace(id='w'))
        return app

    with TestClient(app_instance()) as first_api:
        first = first_api.post('/api/v1/worker/executions/e/recovery/reconcile', json=initial)
        assert first.status_code == 200 and first.json()['action'] == 'track'
        # The committed reply never reaches the Worker.
    with factory() as session:
        run = session.get(Run, 'r')
        original_lease = run.lease_expires_at
        run.status = 'CANCEL_REQUESTED'
        session.commit()
    with TestClient(app_instance()) as restarted_api:
        replay = restarted_api.post('/api/v1/worker/executions/e/recovery/reconcile', json=initial)
        assert replay.status_code == 200
        assert replay.json()['duplicate'] and replay.json()['action'] == 'stop'
        assert replay.json()['reason'] == 'cancelled'
        fresh = restarted_api.post('/api/v1/worker/executions/e/recovery/reconcile',
                                   json=observation(generation, sequence=2).model_dump())
        assert fresh.status_code == 200 and fresh.json()['action'] == 'stop'
    with factory() as session:
        run = session.get(Run, 'r')
        assert run.status == 'CANCEL_REQUESTED' and run.lease_expires_at == original_lease
        assert not run.cleanup_confirmed


def test_postgres_duplicate_control_concurrency(pg):
    from concurrent.futures import ThreadPoolExecutor
    factory=seed(pg[0])
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(s,run,'w').generation;s.commit()
    def send(_):
        try:return apply(factory,generation)
        except HTTPException as exc:
            assert exc.status_code==503
            return None
    with ThreadPoolExecutor(max_workers=8) as executor:
        replies=list(executor.map(send,range(16)))
    assert sum(r is not None and not r['duplicate'] for r in replies)==1
    assert apply(factory,generation)['duplicate']


def test_postgres_control_migration_guard(pg,monkeypatch):
    from alembic import command
    from alembic.config import Config
    engine,url=pg;monkeypatch.setenv('DATABASE_URL',url);cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'ae185f6');command.upgrade(cfg,'af296a7')
    command.downgrade(cfg,'ae185f6');command.upgrade(cfg,'af296a7')
    factory=seed(engine,create=False)
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(s,run,'w').generation;s.commit()
    apply(factory,generation)
    with pytest.raises(RuntimeError,match='control decisions remain'):command.downgrade(cfg,'ae185f6')
    with engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='af296a7'


@pytest.mark.parametrize('late_state',['running','exited','missing'])
@pytest.mark.parametrize('delay',[1,365*86400])
def test_status_after_tolerance_keeps_evidence_and_resource_hold(execution,monkeypatch,late_state,delay):
    factory,generation=execution;at=now()
    monkeypatch.setattr(control,'now',lambda:at)
    with factory() as s:
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':1}
        s.commit()
    assert apply(factory,generation)['action']=='track'
    monkeypatch.setattr(control,'now',lambda:at+timedelta(seconds=delay))
    late=apply(factory,generation,sequence=2,state=late_state,exit_code=0 if late_state=='exited' else None)
    assert late['action']==('stop' if late_state=='running' else 'retain')
    with factory() as s:
        run=s.get(Run,'r');snapshot=s.get(ExecutionRecoveryControl,'e').snapshot
        assert run.status=='RUNNING' and not run.cleanup_confirmed
        assert snapshot['observed_state']==late_state
        if late_state=='exited':assert snapshot['exit_code']==0
    # No fresh execution and no implicit resurrection, even if reports resume.
    again=apply(factory,generation,sequence=3)
    assert again['action']==('stop' if late_state=='running' else 'inspect')


@pytest.mark.parametrize('delay', [0.999,1.0,1.001])
def test_finite_cutoff_boundary(execution,monkeypatch,delay):
    factory,generation=execution;at=now()
    monkeypatch.setattr(control,'now',lambda:at)
    with factory() as s:
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':1};s.commit()
    apply(factory,generation)
    monkeypatch.setattr(control,'now',lambda:at+timedelta(seconds=delay))
    assert apply(factory,generation,sequence=2)['action']==('track' if delay<1 else 'stop')


def test_unlimited_disconnection_does_not_impose_an_implicit_cutoff(execution,monkeypatch):
    factory,generation=execution;at=now()
    monkeypatch.setattr(control,'now',lambda:at)
    with factory() as s:s.get(Job,'j').client_spec={'timeout_seconds':0};s.commit()
    apply(factory,generation)
    monkeypatch.setattr(control,'now',lambda:at+timedelta(days=365))
    assert apply(factory,generation,sequence=2,elapsed_seconds=365*86400+10)['action']=='track'


@pytest.mark.parametrize('intent',['cancel','revoke','terminal'])
def test_lost_response_replay_cannot_hide_new_stop_intent(execution,intent):
    factory,generation=execution
    apply(factory,generation)
    with factory() as s:
        deadline=s.get(Run,'r').lease_expires_at
        if intent=='revoke':s.get(ExecutionRecovery,'e').revoked=True
        else:s.get(Run,'r').status='CANCEL_REQUESTED' if intent=='cancel' else 'TIMED_OUT'
        s.commit()
    response=apply(factory,generation)
    assert response['duplicate'] and response['action']=='stop'
    with factory() as s:
        assert s.get(Run,'r').lease_expires_at==deadline
        assert s.get(ExecutionRecoveryControl,'e').snapshot['stop_requested']


def test_old_sequence_and_unrepresentable_elapsed_time(execution):
    factory,generation=execution
    apply(factory,generation,sequence=2)
    with pytest.raises(HTTPException,match='stale'):
        apply(factory,generation,sequence=1)
    with pytest.raises(HTTPException,match='elapsed time'):
        apply(factory,generation,sequence=3,elapsed_seconds=1e100)


def test_postgres_cancel_lock_contention_and_retry(pg):
    from backend.app.scheduling import lock
    factory=seed(pg[0])
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        generation=recovery_records.grant(s,run,'w').generation;s.commit()
    apply(factory,generation)
    with factory() as cancelling:
        lock(cancelling)
        cancelling.get(Run,'r').status='CANCEL_REQUESTED'
        cancelling.flush()
        with pytest.raises(HTTPException) as exc:apply(factory,generation,sequence=2)
        assert exc.value.status_code==503
        cancelling.commit()
    assert apply(factory,generation,sequence=2)['action']=='stop'


def test_http_to_worker_reconciliation_persists_stop_after_deadline(execution,monkeypatch):
    from types import SimpleNamespace
    from worker.disconnection import DisconnectionClock
    from worker.recovery_control import RecoveryControl
    factory,generation=execution;at=now()
    with factory() as s:
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':10};s.commit()
    def db():
        with factory() as s:yield s
    app=FastAPI();control.install(app,db,lambda:SimpleNamespace(id='w'))
    local=RecoveryControl(DisconnectionClock('e',generation,'boot-one',10,0))
    with TestClient(app) as client:
        monkeypatch.setattr(control,'now',lambda:at)
        first=client.post('/api/v1/worker/executions/e/recovery/reconcile',
            json=observation(generation).model_dump()).json()
        saved=[]
        assert local.apply(first,sequence=1,requested_at=0,received_at=1,persist=saved.append)
        monkeypatch.setattr(control,'now',lambda:at+timedelta(days=365))
        late=client.post('/api/v1/worker/executions/e/recovery/reconcile',
            json=observation(generation,sequence=2).model_dump()).json()
        assert late['action']=='stop'
        assert local.apply(late,sequence=2,requested_at=365*86400,received_at=365*86400+1,persist=saved.append)
        assert local.directive=='stop'
        restored=RecoveryControl.restore(saved[-1],execution_id='e',generation=generation,boot_id='boot-one')
        assert restored.directive=='stop'
    with factory() as s:assert not s.get(Run,'r').cleanup_confirmed


def test_missing_finite_control_origin_is_not_reset_or_a_server_error(execution):
    factory,generation=execution
    with factory() as s:
        s.get(Run,'r').enqueued_at=None
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':10}
        s.commit()
    response=apply(factory,generation)
    assert response['action']=='inspect' and response['reason']=='missing_original_control_time'


@pytest.mark.parametrize('kind,size',[('host',32),('docker',64)])
@pytest.mark.parametrize('tolerance',[None,120])
def test_heartbeat_expiry_then_repeated_recovery_preserves_execution(execution,monkeypatch,kind,size,tolerance):
    """Exercise scheduler, API decision and durable node guard together.

    Runtime identities represent both backends; this is not a real container
    or systemd launch test.
    """
    from backend.app import scheduling
    from worker.disconnection import DisconnectionClock
    from worker.recovery_control import RecoveryControl
    factory,generation=execution;at=now()
    identity={'kind':kind,'boot_id':'boot-one','runtime_id':'a'*size}
    with factory() as s:
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':tolerance}
        s.get(Run,'r').enqueued_at=at
        s.get(Job,'j').client_spec={'timeout_seconds':0}
        s.commit()
    local=RecoveryControl(DisconnectionClock('e',generation,'boot-one',tolerance,0))
    saved=[]
    for sequence,elapsed in enumerate((0,90,180),1):
        current=at+timedelta(seconds=elapsed)
        monkeypatch.setattr(control,'now',lambda:current)
        if elapsed:
            with factory() as s:
                assert control.utc(s.get(Run,'r').lease_expires_at)<current
                scheduling.reconcile(s,current);s.commit()
                assert s.get(Run,'r').status=='RUNNING'
                assert not s.get(Run,'r').cleanup_confirmed
            assert local.clock.check(elapsed) is None
        reply=apply(factory,generation,sequence=sequence,identity=identity,elapsed_seconds=float(elapsed))
        assert reply['action']=='track'
        assert local.apply(reply,sequence=sequence,requested_at=elapsed,received_at=elapsed+1,persist=saved.append)
        local=RecoveryControl.restore(saved[-1],execution_id='e',generation=generation,boot_id='boot-one')
        with factory() as s:
            run=s.get(Run,'r')
            assert run.execution_id=='e' and run.slot_id=='s'
            assert control.utc(run.started_at)==at
    final=apply(factory,generation,sequence=4,identity=identity,state='exited',exit_code=0,elapsed_seconds=181)
    assert final['action']=='retain'
    with factory() as s:
        assert s.get(ExecutionRecoveryControl,'e').snapshot['exit_code']==0
        assert not s.get(Run,'r').cleanup_confirmed


@pytest.mark.parametrize('platform_status,reason',[
    ('CANCEL_REQUESTED','cancelled'),('CANCELLED','cancelled'),
    ('TIMED_OUT','execution_timeout'),('FAILED','platform_terminal'),
    ('SUCCEEDED','platform_terminal'),
])
@pytest.mark.parametrize('late_state',['running','exited'])
def test_platform_lifecycle_wins_over_late_worker_success(execution,platform_status,reason,late_state):
    factory,generation=execution
    apply(factory,generation)
    # The node previously stopped locally, but the platform's accepted control
    # state, not the node's opinion, determines the final lifecycle.
    apply(factory,generation,sequence=2,stop_reason='disconnection_timeout')
    with factory() as s:
        s.get(Run,'r').status=platform_status
        s.get(Job,'j').status=platform_status
        s.commit()
    response=apply(factory,generation,sequence=3,state=late_state,
                   exit_code=0 if late_state=='exited' else None,stop_reason='disconnection_timeout')
    assert response['action']=='stop' and response['reason']==reason
    assert response['platform_status']==platform_status
    with factory() as s:
        assert s.get(Run,'r').status==s.get(Job,'j').status==platform_status
        snapshot=s.get(ExecutionRecoveryControl,'e').snapshot
        assert snapshot['worker_stop_reason']=='disconnection_timeout'
        if late_state=='exited':assert snapshot['exit_code']==0
        assert not s.get(Run,'r').cleanup_confirmed


def test_maximum_accepted_tolerance_does_not_overflow_datetime(execution):
    factory,generation=execution
    with factory() as s:
        s.get(ExecutionRecovery,'e').policy={'version':1,'disconnect_tolerance_seconds':2**63-1}
        s.commit()
    assert apply(factory,generation)['action']=='track'
