from sqlalchemy import select
from backend.tests.test_execution import env
from backend.tests.test_batch_controls import setup, control
from backend.app.core import EvaluationBatch, Job, Run, Event, ManagementAudit, ServiceInstance


def delete(env, batch, owner='alice'):
    _, client, auth = env
    return client.delete('/api/v1/batches/'+batch['id'], headers=auth[owner])


def test_authorization_terminal_cleanup_deletes_tasks_and_retains_tombstone_audit(env):
    main, c, auth = env
    b, _, _ = setup(env)
    assert delete(env, b, 'bob').status_code == 404
    control(env, b, 'cancel', 0)
    with main.SessionLocal() as s:
        s.get(Run, b['cases'][0]['run_id']).cleanup_confirmed = False
        s.commit()
    pending = delete(env, b)
    assert pending.status_code == 200 and pending.json()['pending_cleanup'] is True
    with main.SessionLocal() as s:
        s.get(Run, b['cases'][0]['run_id']).cleanup_confirmed = True
        job_id = b['cases'][0]['job_id']
        run_id = b['cases'][0]['run_id']
        event_count = len(s.scalars(select(Event).where(Event.job_id == job_id)).all())
        audit_count = len(s.scalars(select(ManagementAudit).where(ManagementAudit.target_id == b['id'])).all())
        s.commit()
    from backend.app.scheduling import tick
    tick(main.SessionLocal)
    assert delete(env, b).status_code == 200
    assert delete(env, b, 'admin').status_code == 200
    assert delete(env, b, 'bob').status_code == 404
    assert c.get('/api/v1/batches', headers=auth['alice']).json() == []
    for suffix in ('', '/monitor', '/report'):
        assert c.get('/api/v1/batches/'+b['id']+suffix, headers=auth['alice']).status_code == 404
    assert control(env, b, 'resume', 2).status_code == 404
    assert c.post('/api/v1/jobs/'+b['cases'][0]['job_id']+'/retry', headers=auth['alice']).status_code == 404
    with main.SessionLocal() as s:
        assert s.get(EvaluationBatch, b['id']).deleted_at is not None
        assert s.get(Job, job_id) is None
        assert s.get(Run, run_id) is None
        assert len(s.scalars(select(Event).where(Event.job_id == job_id)).all()) == 0
        assert audit_count >= 1
        assert len(s.scalars(select(ManagementAudit).where(ManagementAudit.target_id == b['id'])).all()) == audit_count
        assert event_count > 0


def test_batch_deletion_removes_orchestration_tasks_and_services(env, monkeypatch):
    from backend.tests.test_orchestrations import setup as managed_setup, submit
    main, c, auth = env
    *_, body = managed_setup(env, monkeypatch)
    batch = submit(env, body, 'delete-orchestration-tasks')
    control(env, batch, 'cancel', 0)
    with main.SessionLocal() as s:
        services = list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id == batch['id'])))
        for item in services:
            item.desired_running = False
        s.commit()
    assert delete(env, batch).status_code == 200
    with main.SessionLocal() as s:
        assert not s.scalars(select(Job).where(Job.batch_id == batch['id'])).all()
        assert not s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id == batch['id'])).all()


def test_delete_active_orchestration_stops_related_service_and_keeps_uncleaned_run(env, monkeypatch):
    from backend.tests.test_orchestrations import setup as managed_setup, submit
    from backend.app.scheduling import tick
    main, client, auth = env
    server_token, server_slot, *_, body = managed_setup(env, monkeypatch)
    batch = submit(env, body, 'delete-active-orchestration')
    tick(main.SessionLocal)
    from backend.tests.test_execution import claim
    claimed = claim(env, server_token, server_slot)
    assert claimed and claimed['benchmark'] == 'inference-service'
    with main.SessionLocal() as session:
        services = list(session.scalars(select(ServiceInstance).where(
            ServiceInstance.orchestration_batch_id == batch['id'])))
        assert services and any(item.desired_running for item in services)
        service_id = services[0].id
        service_job_id = services[0].current_job_id
        assert service_job_id
    reply = delete(env, batch)
    assert reply.status_code == 200 and reply.json()['pending_cleanup']
    assert client.get('/api/v1/batches', headers=auth['alice']).json() == []
    with main.SessionLocal() as session:
        service = session.get(ServiceInstance, service_id)
        assert service is not None and not service.desired_running
        assert session.get(Job, service_job_id).status in ('CANCELLED', 'CANCEL_REQUESTED')
        assert session.scalar(select(Run.id).where(Run.job_id == service_job_id))
    tick(main.SessionLocal)
    with main.SessionLocal() as session:
        assert session.get(ServiceInstance, service_id) is not None
    assert delete(env, batch).json()['pending_cleanup']


def test_idempotent_submission_cannot_revive_deleted_batch(env):
    from backend.tests.test_cs_protocol import batch_body, template
    _, c, auth = env
    body = batch_body(template(env))
    headers = {**auth['alice'], 'Idempotency-Key':'deleted-test'}
    b = c.post('/api/v1/batches', headers=headers, json=body).json()
    control(env, b, 'cancel', 0)
    assert delete(env, b).status_code == 200
    assert c.post('/api/v1/batches', headers=headers, json=body).status_code == 409


def test_service_stop_requested_and_service_restart_guard(env):
    main, c, auth = env
    b, _, slots = setup(env)
    control(env, b, 'cancel', 0)
    with main.SessionLocal() as s:
        from backend.app.core import Slot
        slot = s.get(Slot, slots[0])
        item = ServiceInstance(name='managed',worker_id=slot.worker_id,slot_id=slot.id,address='localhost',port=9000,template_snapshot={},orchestration_batch_id=b['id'])
        s.add(item);s.commit();identity=item.id
    assert delete(env,b).status_code == 200
    with main.SessionLocal() as s:
        item=s.get(ServiceInstance,identity)
        assert item is None or item.desired_running is False
    response=c.post('/api/v1/admin/services/'+identity+'/control',headers=auth['admin'],json={'action':'start','expected_revision':0})
    assert response.status_code==404


def test_deleted_orchestration_cannot_read_restart_or_replay(env, monkeypatch):
    from backend.tests.test_orchestrations import setup as managed_setup, submit
    _, c, auth = env
    *_, body = managed_setup(env, monkeypatch)
    batch = submit(env, body, 'deleted-orchestration')
    control(env, batch, 'cancel', 0)
    assert delete(env, batch).status_code == 200
    assert c.get('/api/v1/orchestrations/'+batch['id'], headers=auth['alice']).status_code == 404
    response=c.post('/api/v1/orchestrations', headers={**auth['alice'],'Idempotency-Key':'deleted-orchestration'},json=body)
    assert response.status_code == 409 and response.json()['detail'] == 'batch has been deleted'


def test_all_attempts_must_be_clean_before_deletion(env):
    main, c, auth = env
    batch, _, _ = setup(env)
    control(env, batch, 'cancel', 0)
    job_id=batch['cases'][0]['job_id'];first=batch['cases'][0]['run_id']
    assert c.post('/api/v1/jobs/'+job_id+'/retry',headers=auth['alice']).status_code==202
    control(env,batch,'cancel',1)
    with main.SessionLocal() as s:
        s.get(Run,first).cleanup_confirmed=False;s.commit()
    response=delete(env,batch)
    assert response.status_code==200 and response.json()['pending_cleanup'] is True
    assert c.get('/api/v1/batches',headers=auth['alice']).json()==[]
    with main.SessionLocal() as s:
        assert s.get(Run,first) is not None
