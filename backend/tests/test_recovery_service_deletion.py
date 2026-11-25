"""A lost service heartbeat cannot make a deleted batch ready again."""
from datetime import timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.app import services
from backend.app.batch_deletion import remove
from backend.app.core import Job, Run, ServiceInstance, Slot, User, Worker, now
from backend.app.service_dependencies import ready
from backend.app.scheduling import tick
from backend.tests.test_recovery_control import execution, apply as observe, observation
from backend.tests.test_recovery_finish import complete, cleanup, sync_results
from backend.app.execution import RecoveryProof
from backend.tests.test_execution_logs_postgres import seed


def test_disconnected_service_reprobes_then_delete_keeps_stop_authority(execution):
    factory, generation = execution
    with factory() as session:
        job = session.get(Job, 'j')
        job.benchmark = 'inference-service'
        job.service_instance_id = 'svc'
        run = session.get(Run, 'r')
        run.lease_expires_at = now() - timedelta(minutes=1)
        session.get(Slot, 's').last_seen_at = now()
        session.add(ServiceInstance(id='svc', name='svc', worker_id='w', slot_id='s',
            address='127.0.0.1', port=19000, current_job_id='j',
            orchestration_batch_id='b', template_snapshot={'readiness': {'kind': 'tcp'}},
            desired_running=True, health_ready=True, health_at=now(),
            probe_state={'epoch': 'old', 'sequence': 20, 'ever_ready': True}))
        session.commit()
    assert observe(factory, generation, state='running')['action'] == 'track'
    with factory() as session:
        item = session.get(ServiceInstance, 'svc')
        assert not ready(session, item)
        assert item.probe_state['epoch'] is None
    app = FastAPI()
    def database():
        with factory() as session:
            yield session
    services.install(app, database, lambda: SimpleNamespace(id='u', role='admin'),
                     lambda: SimpleNamespace(id='w'))
    with TestClient(app) as client:
        path = '/api/v1/worker/executions/e/'
        session_reply = client.post(path + 'service-probe-session')
        assert session_reply.status_code == 200, session_reply.text
        epoch = session_reply.json()['epoch']
        health = {'ready': True, 'epoch': epoch, 'sequence': 1}
        response = client.post(path + 'service-health', json=health)
        assert response.status_code == 200 and response.json()['ready']
        with factory() as session:
            assert ready(session, session.get(ServiceInstance, 'svc'))
            assert remove(session, 'b', session.get(User, 'u'))['pending_cleanup']
            session.commit()
        # A delayed old probe cannot revive the service after deletion.
        stale = client.post(path + 'service-health', json={**health, 'epoch': 'old', 'sequence': 21})
        assert stale.status_code == 409
        with factory() as session:
            item = session.get(ServiceInstance, 'svc')
            assert not item.desired_running and not ready(session, item)
            assert session.get(Run, 'r') is not None
    assert observe(factory, generation, sequence=2, state='exited', exit_code=0)['action'] == 'stop'
    proof = RecoveryProof(generation=generation, identity=observation(generation).identity)
    assert complete(factory, proof, pending_results=True)['status'] == 'CANCELLED'
    assert cleanup(factory, proof)['cleanup_confirmed']
    assert cleanup(factory, proof)['duplicate']  # response lost, original stop confirmation replayed
    assert sync_results(factory, proof)['result_sync'] == 'complete'
    tick(factory)
    with factory() as session:
        assert session.get(ServiceInstance, 'svc') is None
        assert session.get(Run, 'r') is None


def test_one_side_cleanup_cannot_purge_other_disconnected_execution():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    factory = seed(engine)
    try:
        with factory() as session:
            service_job = session.get(Job, 'j')
            service_job.benchmark = 'inference-service'
            service_job.service_instance_id = 'svc'
            session.add(ServiceInstance(id='svc', name='svc', worker_id='w', slot_id='s',
                address='127.0.0.1', port=19000, current_job_id='j',
                orchestration_batch_id='b', template_snapshot={'protocol': 'http'},
                desired_running=True))
            session.add(Worker(id='client-worker', node_name='client-worker', token_hash='client-unused'))
            session.flush()
            session.add(Slot(id='client-slot', worker_id='client-worker', pool_id='p',
                node_name='client-worker', queue_name='client-worker', gpu_index=0,
                gpu_uuid='virtual-client', gpu_model='virtual'))
            session.add(Job(id='client-job', batch_id='b', owner_id='u', pool_id='p',
                title='client', benchmark='cs-client', model_ref='m', episodes=1,
                status='RUNNING'))
            session.flush()
            session.add(Run(id='client-run', job_id='client-job', attempt=1,
                slot_id='client-slot', execution_id='client-execution',
                status='RUNNING', cleanup_confirmed=False,
                lease_expires_at=now()+timedelta(minutes=2)))
            session.commit()
        with factory() as session:
            assert remove(session, 'b', session.get(User, 'u'))['pending_cleanup']
            session.commit()
        with factory() as session:
            session.get(Run, 'r').status = session.get(Job, 'j').status = 'CANCELLED'
            session.get(Run, 'r').cleanup_confirmed = True
            session.commit()
        with factory() as session:
            assert remove(session, 'b', session.get(User, 'u'))['pending_cleanup']
            assert session.get(ServiceInstance, 'svc') is not None
            assert session.get(Run, 'client-run') is not None
            session.commit()
        with factory() as session:
            session.get(Run, 'client-run').status = session.get(Job, 'client-job').status = 'CANCELLED'
            session.get(Run, 'client-run').cleanup_confirmed = True
            session.commit()
        with factory() as session:
            assert not remove(session, 'b', session.get(User, 'u'))['pending_cleanup']
            session.commit()
        with factory() as session:
            assert session.get(ServiceInstance, 'svc') is None
            assert session.get(Run, 'client-run') is None
    finally:
        engine.dispose()
