from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.pool import StaticPool
from alembic import command
from alembic.config import Config

from backend.app import recovery_records as records
from backend.app.core import EvaluationBatch, ExecutionRecovery, ExecutionRecoveryReceipt, Run, Slot, Worker, now
from backend.tests.test_execution_logs_postgres import seed, pg, api as pg_api


@pytest.fixture
def ledger():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    factory = seed(engine)
    with factory() as s:
        run = s.get(Run, 'r')
        run.status = 'STARTING'
        entry = records.grant(s, run, 'w', 60)
        generation = entry.generation
        s.commit()
    yield factory, generation
    engine.dispose()


def receive(s, original_generation, **changes):
    args = dict(worker_id='w', execution_id='e', generation=original_generation,
                kind='episode', sequence=1, body={'status': 'COMPLETED'})
    args.update(changes)
    return records.receive(s, **args)


def test_expired_lease_and_terminal_reports_do_not_restore_execution(ledger):
    factory, generation = ledger
    for status in ('RUNNING', 'TIMED_OUT', 'CANCELLED', 'SUCCEEDED'):
        with factory() as s:
            run = s.get(Run, 'r')
            run.status = status
            run.lease_expires_at = now() - timedelta(days=365)
            original = run.lease_expires_at
            reply = receive(s, generation)
            s.commit()
            assert not reply['reconciled']
            assert run.status == status and not run.cleanup_confirmed
            assert run.lease_expires_at == original


def test_ack_replay_and_digest_conflict(ledger):
    factory, generation = ledger
    with factory() as s:
        first = receive(s, generation)
        s.commit()
    with factory() as s:
        duplicate = receive(s, generation)
        assert duplicate == {**first, 'duplicate': True}
        with pytest.raises(HTTPException) as exc:
            receive(s, generation, body={'status': 'ERROR'})
        assert exc.value.status_code == 409
        s.rollback()
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt)) == 1
        assert s.get(ExecutionRecovery, 'e').received_records == 1


@pytest.mark.parametrize('changes,code', [({'worker_id':'other'},404),
    ({'execution_id':'missing'},404), ({'generation':'old'},409), ({'kind':'logs'},422),
    ({'sequence':True},422), ({'body':[]},422), ({'body':{'value':float('nan')}},422),
    ({'body':{'value':'x'*65536}},413)])
def test_invalid_identity_and_message(ledger, changes, code):
    factory, generation = ledger
    with factory() as s, pytest.raises(HTTPException) as exc:
        receive(s, generation, **changes)
    assert exc.value.status_code == code


@pytest.mark.parametrize('deleted', [False, True])
def test_revocation_or_deleted_run_accepts_only_safety_evidence(ledger, deleted):
    factory, generation = ledger
    with factory() as s:
        if deleted:
            s.delete(s.get(Run, 'r'))
        else:
            s.get(ExecutionRecovery, 'e').revoked = True
        s.commit()
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            receive(s, generation)
        assert exc.value.status_code == 410
        s.rollback()
        result = receive(s, generation, kind='control', body={'stopped': True})
        s.commit()
        assert result['reconciled'] is False


def test_old_assignment_cannot_write_new_attempt(ledger):
    factory, generation = ledger
    with factory() as s:
        s.get(Run, 'r').execution_id = 'new-execution'
        s.commit()
    with factory() as s, pytest.raises(HTTPException) as exc:
        receive(s, generation)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('change,code', [('slot_owner',409), ('deleted_batch',410)])
def test_receipt_only_cannot_accept_business_data_after_identity_or_deletion_change(ledger,change,code):
    factory,generation=ledger
    with factory() as s:
        if change=='slot_owner':
            s.get(Slot,'s').worker_id='other'
        else:
            s.get(EvaluationBatch,'b').deleted_at=now()
        s.commit()
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            receive(s,generation)
        assert exc.value.status_code==code
        s.rollback()
        # The original identity may still send bounded safety evidence; it
        # cannot gain business authority merely by replaying a receipt.
        assert receive(s,generation,kind='control',body={'stopped':True})['reconciled'] is False
        s.commit()
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt).where(
            ExecutionRecoveryReceipt.kind=='episode'))==0


def test_control_capacity_reserved_and_duplicates_allowed_when_full(ledger, monkeypatch):
    factory, generation = ledger
    monkeypatch.setattr(records, 'MAX_RECORDS', 2)
    monkeypatch.setattr(records, 'CONTROL_RECORDS', 1)
    with factory() as s:
        receive(s, generation)
        assert receive(s, generation)['duplicate']
        with pytest.raises(HTTPException) as exc:
            receive(s, generation, sequence=2)
        assert exc.value.status_code == 413
        receive(s, generation, kind='control')
        with pytest.raises(HTTPException):
            receive(s, generation, kind='control', sequence=2)
        s.commit()
    with factory() as s:
        assert s.get(ExecutionRecovery, 'e').received_records == 2


def test_byte_budget_and_transaction_rollback(ledger, monkeypatch):
    factory, generation = ledger
    monkeypatch.setattr(records, 'MAX_BYTES', 32)
    monkeypatch.setattr(records, 'CONTROL_BYTES', 16)
    with factory() as s, pytest.raises(HTTPException) as exc:
        receive(s, generation)
    assert exc.value.status_code == 413
    with factory() as s:
        receive(s, generation, body={})
        s.rollback()  # An uncommitted receipt must not become an ACK.
    with factory() as s:
        assert not receive(s, generation, body={})['duplicate']


def test_grant_immutable_and_legacy_not_implicitly_enabled(ledger):
    factory, generation = ledger
    with factory() as s:
        run = s.get(Run, 'r')
        assert records.grant(s, run, 'w', 60).generation == generation
        for tolerance in (None, 0, -1, True):
            with pytest.raises(HTTPException):
                records.grant(s, run, 'w', tolerance)
        s.get(ExecutionRecovery, 'e').revoked = True
        with pytest.raises(HTTPException):
            records.grant(s, run, 'w', 60)


@pytest.mark.parametrize("policy_args, expected", [((), None), ((None,), None), ((120,), 120)])
def test_fresh_grant_requires_claim_and_ownership(ledger, policy_args, expected):
    factory, _ = ledger
    with factory() as s:
        run = Run(id='r2', job_id='j', attempt=2, execution_id='e2', slot_id='s',
                  status='RESERVED', cleanup_confirmed=True, allocation_index=1)
        s.add(run); s.flush()
        with pytest.raises(HTTPException):
            records.grant(s, run, 'other', None)
        with pytest.raises(HTTPException):
            records.grant(s, run, 'w', None)
        run.status='STARTING'; run.cleanup_confirmed=False
        assert records.grant(s, run, 'w', *policy_args).policy['disconnect_tolerance_seconds'] == expected


def test_http_receipt_commits_before_ack(ledger):
    factory, generation = ledger
    app = FastAPI()
    def db():
        with factory() as s:
            yield s
    def auth():
        return Worker(id='w')
    records.install(app, db, auth)
    with TestClient(app) as client:
        for suffix, extra in [('control', {}), ('results', {'kind':'episode'})]:
            body = {'generation':generation, 'sequence':1, 'body':{'ok':True}, **extra}
            path = '/api/v1/worker/executions/e/recovery/'+suffix
            assert client.post(path, json=body).status_code == 200
            assert client.post(path, json=body).json()['duplicate']
        assert client.post('/api/v1/worker/executions/e/recovery/control', json={
            'generation':generation, 'sequence':True, 'body':{}}).status_code == 422
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt)) == 2


def test_postgres_concurrent_replay_and_unrelated_scheduler_lock(pg):
    from backend.app.scheduling import lock
    factory = seed(pg[0])
    with factory() as s:
        run=s.get(Run,'r'); run.status='STARTING'
        generation=records.grant(s,run,'w',60).generation
        s.commit()
    def report(_):
        with factory() as s:
            result=receive(s,generation);s.commit();return result
    with factory() as scheduler, ThreadPoolExecutor(max_workers=8) as pool:
        lock(scheduler)
        replies=list(pool.map(report, range(16)))
    assert sum(not r['duplicate'] for r in replies)==1
    with factory() as s:
        assert s.get(ExecutionRecovery,'e').received_records==1


def test_postgres_migration_and_safe_downgrade(pg, monkeypatch):
    engine,url=pg
    monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'ad074e5')
    factory=seed(engine,create=False)
    command.upgrade(cfg,'ae185f6')
    with factory() as s:
        assert s.get(ExecutionRecovery,'e') is None
        assert s.get(Run,'r').status=='RUNNING'
    command.downgrade(cfg,'ad074e5')
    command.upgrade(cfg,'ae185f6')
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        records.grant(s,run,'w',None);s.commit()
    with pytest.raises(RuntimeError,match='recovery records remain'):
        command.downgrade(cfg,'ad074e5')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM execution_recovery')).scalar()==1
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='ae185f6'


@pytest.mark.parametrize('lost_ack', [False, True])
def test_worker_queue_to_http_receipts_after_long_disconnect(ledger, tmp_path, lost_ack):
    from worker.reliable_outbox import ReliableOutbox
    from worker.recovery_delivery import RecoveryDelivery
    factory, generation = ledger
    app = FastAPI()
    def db():
        with factory() as s:
            yield s
    records.install(app, db, lambda: Worker(id='w'))
    q = ReliableOutbox(tmp_path)
    q.put('e', generation, 'episode', 1, {'status':'STARTED'})
    q.put('e', generation, 'episode', 2, {'status':'COMPLETED'})
    q.put('e', generation, 'control', 1, {'exit_code':0})
    with factory() as s:
        s.get(Run,'r').lease_expires_at=now()-timedelta(days=365)
        s.commit()
    with TestClient(app) as client:
        class API:
            offline = True
            drop_ack = lost_ack
            def post(self, path, body):
                if self.offline:
                    raise TimeoutError('offline')
                response = client.post('/api/v1/'+path, json=body)
                assert response.status_code == 200, response.text
                if self.drop_ack:
                    self.drop_ack=False
                    raise TimeoutError('response lost after commit')
                return response.json()
        api=API()
        with pytest.raises(TimeoutError):
            RecoveryDelivery(api,q).step()
        assert len(q.pending())==3
        api.offline=False
        q=ReliableOutbox(tmp_path)  # New local delivery object after restart.
        if lost_ack:
            with pytest.raises(TimeoutError):
                RecoveryDelivery(api,q).step()
            assert len(q.pending())==3
        assert RecoveryDelivery(api,q).step()==3
        assert RecoveryDelivery(api,q).step()==0
    with factory() as s:
        assert s.scalar(select(func.count()).select_from(ExecutionRecoveryReceipt))==3
        run=s.get(Run,'r')
        assert run.status=='STARTING' and not run.cleanup_confirmed
        assert run.lease_expires_at < now().replace(tzinfo=None)


@pytest.mark.parametrize('response', [None, {}, {'digest':'wrong'}])
def test_mismatched_ack_never_discards_local_report(tmp_path, response):
    from worker.reliable_outbox import ReliableOutbox, OutboxConflict
    from worker.recovery_delivery import RecoveryDelivery
    q=ReliableOutbox(tmp_path)
    q.put('e','g','control',1,{'exit_code':0})
    class API:
        def post(self, path, body):return response
    with pytest.raises(OutboxConflict):
        RecoveryDelivery(API(),q).step()
    assert len(ReliableOutbox(tmp_path).pending())==1


def test_real_api_authentication_and_no_implicit_grant(pg_api):
    client, headers, factory = pg_api
    path='/api/v1/worker/executions/e/recovery/control'
    body={'generation':'unknown','sequence':1,'body':{'exit_code':0}}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,headers=headers,json=body).status_code==404
    with factory() as s:
        run=s.get(Run,'r');run.status='STARTING'
        body['generation']=records.grant(s,run,'w',60).generation
        run.lease_expires_at=now()-timedelta(days=365)
        s.commit()
    assert client.post(path,headers=headers,json=body).status_code==200
    with factory() as s:
        s.get(Worker,'w').revoked_at=now();s.commit()
    assert client.post(path,headers=headers,json=body).status_code==401


def test_original_terminal_receipt_survives_worker_credential_rotation(pg_api):
    from backend.app.execution import token_hash
    client, old_headers, factory = pg_api
    path = '/api/v1/worker/executions/e/recovery/control'
    with factory() as session:
        run = session.get(Run, 'r')
        run.status = 'STARTING'
        run.lease_expires_at = now() - timedelta(days=1)
        generation = records.grant(session, run, 'w').generation
        run.status = 'CANCELLED'
        original_lease = run.lease_expires_at
        session.commit()
    body = {'generation': generation, 'sequence': 1, 'body': {'stopped': True}}
    first = client.post(path, headers=old_headers, json=body)
    assert first.status_code == 200 and not first.json()['duplicate']
    new_headers = {'Authorization': 'Bearer wk_rotated_local_test'}
    with factory() as session:
        session.get(Worker, 'w').token_hash = token_hash('wk_rotated_local_test')
        session.commit()
    assert client.post(path, headers=old_headers, json=body).status_code == 401
    replay = client.post(path, headers=new_headers, json=body)
    assert replay.status_code == 200 and replay.json()['duplicate']
    with factory() as session:
        run = session.get(Run, 'r')
        assert run.status == 'CANCELLED' and run.lease_expires_at == original_lease
        assert not run.cleanup_confirmed
        assert session.get(ExecutionRecovery, 'e').received_records == 1
