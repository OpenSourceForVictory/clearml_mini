import base64
import json
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.app.core import EvaluationBatch, Job, Run, Slot
from backend.app.scheduling import tick, transition


def template(env):
    _, c, auth = env
    r = c.post('/api/v1/admin/client-templates', headers=auth['admin'], json={
        'name': 'HTTP protocol check', 'version': '1', 'image': 'approved:cs',
        'command': ['python', '-m', 'runner.cs_demo'], 'protocols': ['http']})
    assert r.status_code == 201, r.text
    return r.json()['id']


def client_config(t):
    return {'template_id': t, 'endpoint': 'http://192.168.137.223:19090/infer',
            'task_name': 'demo', 'seed': 7, 'timeout_seconds': 30}


def submit_body(t):
    return {'title': 'CS', 'benchmark': 'cs-client', 'pool_id': 'mock', 'episodes': 2, 'client': client_config(t)}


def prepare(env):
    main, c, auth = env
    t = template(env)
    token, slots = node(env)
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0]); slot.protocol_version = 3; slot.client_images = ['approved:cs']; s.commit()
    r = c.post('/api/v1/jobs', headers=auth['alice'], json=submit_body(t))
    assert r.status_code == 201, r.text
    assert tick(main.SessionLocal) == 1
    task = c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token).json()['task']
    return t, token, slots[0], r.json()['id'], task


def test_template_permissions_validation_and_immutable_snapshot(env):
    main, c, auth = env
    t = template(env)
    assert c.post('/api/v1/admin/client-templates', headers=auth['alice'], json={
        'name': 'x', 'version': '1', 'image': 'x:1', 'command': ['true']}).status_code == 403
    for url in ['file:///etc/passwd', 'http://localhost:80', 'http://127.0.0.1', 'http://169.254.169.254',
                'http://user:pass@example.com', 'http://example.com/?token=secret', 'http://example.com:99999']:
        body = submit_body(t); body['client']['endpoint'] = url
        assert c.post('/api/v1/jobs', headers=auth['alice'], json=body).status_code == 400
    response = c.post('/api/v1/jobs', headers=auth['alice'], json=submit_body(t))
    assert response.status_code == 201
    jid = response.json()['id']
    c.patch('/api/v1/admin/client-templates/'+t, headers=auth['admin'], json={'enabled': False})
    assert c.post('/api/v1/jobs', headers=auth['alice'], json=submit_body(t)).status_code == 400
    detail = c.get('/api/v1/jobs/'+jid, headers=auth['alice']).json()
    assert detail['client_spec']['template']['enabled'] is True
    assert detail['model_ref'] == 'HTTP protocol check:1'
    assert c.get('/api/v1/client-templates', headers=auth['alice']).json() == []


def test_capability_gate_and_claim_snapshot(env):
    main, c, auth = env
    t = template(env); token, slots = node(env)
    r = c.post('/api/v1/jobs', headers=auth['alice'], json=submit_body(t))
    assert r.status_code == 201
    assert tick(main.SessionLocal) == 0
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0]); slot.protocol_version = 3; slot.client_images = ['other']; s.commit()
    assert tick(main.SessionLocal) == 0
    with main.SessionLocal() as s:
        s.get(Slot, slots[0]).client_images = ['approved:cs']; s.commit()
    assert tick(main.SessionLocal) == 1
    task = c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token).json()['task']
    assert task['image'] == 'approved:cs' and task['timeout_seconds'] == 30
    assert task['client_spec']['seed'] == 7


def test_artifact_idempotency_permissions_and_quotas(env, monkeypatch):
    _, c, auth = env
    _, token, _, jid, task = prepare(env)
    path = f'/api/v1/worker/executions/{task["execution_id"]}/artifacts'
    body = {'name': 'report.json', 'content_base64': base64.b64encode(b'{"ok":true}').decode()}
    first = c.post(path, headers=token, json=body)
    assert first.status_code == 200, first.text
    assert c.post(path, headers=token, json=body).json() == first.json()
    assert c.post(path, headers=token, json={**body, 'content_base64': 'eA=='}).status_code == 409
    for name in ['../secret', '/absolute', 'a/../../x', 'a\\b', 'bad"header']:
        assert c.post(path, headers=token, json={**body, 'name': name}).status_code == 400
    assert c.post(path, headers=token, json={**body, 'name': 'bad', 'content_base64': '!!'}).status_code == 400
    download = f'/api/v1/jobs/{jid}/attempts/{task["run_id"]}/artifacts'
    assert len(c.get(download, headers=auth['alice']).json()) == 1
    assert c.get(download, headers=auth['bob']).status_code == 404
    url = download+'/'+first.json()['id']
    response = c.get(url, headers=auth['alice'])
    assert response.content == b'{"ok":true}' and 'attachment' in response.headers['Content-Disposition']
    assert c.get(url, headers=auth['bob']).status_code == 404
    monkeypatch.setattr('backend.app.execution.MAX_ARTIFACT_COUNT', 1)
    assert c.post(path, headers=token, json={**body, 'name': 'second'}).status_code == 413
    monkeypatch.setattr('backend.app.execution.MAX_ARTIFACT_COUNT', 8)
    monkeypatch.setattr('backend.app.execution.MAX_ARTIFACT_TOTAL', 11)
    assert c.post(path, headers=token, json={**body, 'name': 'second'}).status_code == 413


def batch_body(t):
    return {'title': 'suite', 'pool_id': 'mock', 'client': client_config(t), 'cases': [
        {'case_id': 'a', 'task_name': 'pick', 'episodes': 2, 'seed': 1},
        {'case_id': 'b', 'task_name': 'stack', 'episodes': 3, 'seed': 2}]}


def test_manifest_atomic_idempotent_progress_and_retry(env):
    main, c, auth = env
    t = template(env); body = batch_body(t)
    headers = {**auth['alice'], 'Idempotency-Key': 'suite-once'}
    first = c.post('/api/v1/batches', headers=headers, json=body)
    assert first.status_code == 201, first.text
    batch = first.json()
    assert batch['total'] == 2 and batch['total_episodes'] == 5
    assert c.post('/api/v1/batches', headers=headers, json=body).json()['id'] == batch['id']
    with main.SessionLocal() as s:
        jobs = s.scalars(select(Job).where(Job.batch_id == batch['id']).order_by(Job.case_id)).all()
        assert len(jobs) == 2
        for job, status in zip(jobs, ['SUCCEEDED', 'FAILED']):
            run = s.scalar(select(Run).where(Run.job_id == job.id)); transition(s, run, status)
        failed_id = jobs[1].id
        s.commit()
    url = '/api/v1/batches/'+batch['id']
    b = c.get(url, headers=auth['alice']).json()
    assert b['succeeded'] == 1 and b['finished'] == 2 and b['status'] == 'FAILED'
    assert c.get(url, headers=auth['bob']).status_code == 404
    assert c.post('/api/v1/jobs/'+failed_id+'/retry', headers=auth['alice']).status_code == 202
    b = c.get(url, headers=auth['alice']).json()
    assert b['succeeded'] == 1 and b['finished'] == 1 and b['status'] == 'PENDING'
    cases = {x['case_id']: x for x in b['cases']}
    assert cases['a']['attempt'] == 1 and cases['b']['attempt'] == 2


def test_invalid_manifest_leaves_no_partial_jobs(env, monkeypatch):
    main, c, auth = env
    t = template(env); body = batch_body(t)
    body['cases'][1]['case_id'] = 'a'
    assert c.post('/api/v1/batches', headers=auth['alice'], json=body).status_code == 400
    body = batch_body(t)
    monkeypatch.setenv('MAX_BATCH_BACKLOG_PER_USER', '1')
    assert c.post('/api/v1/batches', headers=auth['alice'], json=body).status_code == 429
    with main.SessionLocal() as s:
        assert not s.scalars(select(EvaluationBatch)).all()
        assert not s.scalars(select(Job)).all()
