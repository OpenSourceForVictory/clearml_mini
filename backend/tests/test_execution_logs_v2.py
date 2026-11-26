import pytest
from datetime import timedelta
from sqlalchemy import select
from backend.tests.test_execution import env, node, submit, claim
from backend.app.core import ExecutionRecovery, Run, RunLogState, RunLogChunk, Slot, now


def running(env):
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    return token, job, task


def test_window_utf8_replay_and_gap(env):
    main, c, auth = env
    token, job, task = running(env)
    path = '/api/v1/worker/executions/'+task['execution_id']+'/logs-v2'
    for i in range(18):
        body = {'sequence': i, 'content': '中'*5000, 'stream': 'stderr'}
        assert c.post(path, headers=token, json=body).status_code == 200
    assert c.post(path, headers=token, json=body).json()['accepted']
    assert c.post(path, headers=token, json={**body, 'content': 'wrong'}).status_code == 409
    assert c.post(path, headers=token, json={'sequence': 0, 'content': 'old'}).json()['resync']
    assert c.post(path, headers=token, json={'sequence': 20, 'content': 'gap'}).status_code == 409
    data = c.get('/api/v1/jobs/'+job+'/logs', headers=auth['alice']).json()
    assert data['version'] == 2 and data['truncated'] and data['gap']
    assert data['first_sequence'] == 1
    assert data['chunks'][-1]['stream'] == 'stderr'
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        state = s.get(RunLogState, run.id)
        assert state.retained_bytes == 255000
        assert run.log_bytes == 0
    assert c.get('/api/v1/jobs/'+job+'/logs', headers=auth['bob']).status_code == 404


@pytest.mark.parametrize('body', [
    {'sequence': 0, 'content': ''}, {'sequence': True, 'content': 'x'},
    {'sequence': 0, 'content': '中'*6000}, {'sequence': 0, 'content': '\x00'},
    {'sequence': 0, 'content': 'x', 'stream': 'bad'},
])
def test_invalid_chunks(env, body):
    _, c, _ = env
    token, _, task = running(env)
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/logs-v2',
                  headers=token, json=body).status_code in (400, 422)


def test_permissions_closed_run_and_empty_gap(env):
    _, c, auth = env
    token, job, task = running(env)
    other, _ = node(env, 'other')
    path = '/api/v1/worker/executions/'+task['execution_id']+'/logs-v2'
    body = {'sequence': 0, 'content': '', 'gap': True, 'stream': 'system'}
    assert c.post(path, headers=other, json=body).status_code == 404
    assert c.post(path, headers=auth['alice'], json=body).status_code == 401
    assert c.post(path, headers=token, json=body).status_code == 200
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',
                  headers=token, json={'status': 'FAILED'}).status_code == 200
    assert c.post(path, headers=token, json=body).status_code == 409
    data = c.get('/api/v1/jobs/'+job+'/logs', headers=auth['alice']).json()
    assert data['complete'] and data['truncated'] and data['chunks'][0]['gap']


def test_expired_lease_allows_only_original_recovery_owner_log_tail(env):
    from backend.app.recovery_records import grant

    main, client, _ = env
    token, _, task = running(env)
    path = '/api/v1/worker/executions/' + task['execution_id'] + '/logs-v2'
    with main.SessionLocal() as session:
        run = session.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        run.lease_expires_at = now() - timedelta(minutes=1)
        session.commit()
    body = {'sequence': 0, 'content': 'OFFLINE_FINISHED', 'stream': 'stdout'}
    assert client.post(path, headers=token, json=body).status_code == 409

    with main.SessionLocal() as session:
        run = session.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        # Recovery grants are issued at original claim, not after expiry.
        run.status = 'STARTING'
        owner = session.get(Slot, run.slot_id).worker_id
        grant(session, run, owner)
        run.status = 'RUNNING'
        session.commit()
    assert client.post(path, headers=token, json=body).status_code == 200
    with main.SessionLocal() as session:
        session.get(ExecutionRecovery, task['execution_id']).revoked = True
        session.commit()
    assert client.post(path, headers=token, json={'sequence': 1, 'content': 'late'}).status_code == 409


def test_metadata_count_bounded(env):
    main, c, _ = env
    token, _, task = running(env)
    # Avoid hundreds of HTTP fixture overheads while exercising the same transaction function.
    from backend.app.execution_logs import append, LogChunk
    from backend.app.core import Worker, Slot
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        worker = s.get(Worker, s.get(Slot, run.slot_id).worker_id)
        for i in range(270):
            append(s, task['execution_id'], worker, LogChunk(sequence=i, content='', gap=True))
        s.commit()
        assert len(s.scalars(select(RunLogChunk)).all()) == 256
        assert s.get(RunLogState, run.id).first_sequence == 14


def test_claim_negotiates_only_compatible_templates_and_software(env):
    from backend.app.core import Slot
    from backend.tests.test_orchestrations import setup as managed_setup, submit as managed_submit
    # Existing mock images have no injected runner: never select v2 just because
    # the Worker understands it.
    main,c,_=env
    token,slots=node(env)
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).software={'capabilities':['bounded_logs_v2']};s.commit()
    submit(env)
    assert 'log_protocol' not in claim(env,token,slots[0])


@pytest.mark.parametrize('capable',[False,True])
def test_injected_runtime_log_capability(env,capable):
    from backend.tests.test_image_runtime import runtime_setup
    from backend.tests.test_cs_protocol import submit_body
    from backend.app.core import Slot
    main,c,auth=env
    template,token,slot,_,report=runtime_setup(env)
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=17
        s.get(Slot,slot).software={'capabilities':['bounded_logs_v2'] if capable else []}
        s.commit()
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'runtime':report}).status_code==200
    response=c.post('/api/v1/jobs',headers=auth['alice'],json=submit_body(template))
    assert response.status_code==201,response.text
    task=claim(env,token,slot)
    assert task is not None
    assert (task.get('log_protocol')==2)==capable
