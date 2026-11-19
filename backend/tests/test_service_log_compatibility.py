"""Standalone service logs must read the same execution window as job logs."""
from sqlalchemy import select
from backend.tests.test_services import env, setup
from backend.tests.test_execution import claim
from backend.app.core import Run, RunLog, RunLogState


def test_service_v2_window_and_legacy_isolation(env):
    main, client, auth = env
    token, slot, body = setup(env)
    created = client.post('/api/v1/admin/services', headers=auth['admin'], json=body).json()
    path = '/api/v1/admin/services/' + created['id'] + '/logs'
    assert client.get(path, headers=auth['admin']).json()['content'] == ''
    task = claim(env, token, slot)
    upload = '/api/v1/worker/executions/' + task['execution_id'] + '/logs-v2'
    with main.SessionLocal() as session:
        run = session.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        session.add(RunLog(run_id=run.id, sequence=0, content='legacy-only\n'))
        session.commit()
    assert client.get(path, headers=auth['admin']).json()['content'] == 'legacy-only\n'
    with main.SessionLocal() as session:
        run = session.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        session.add(RunLogState(run_id=run.id))
        session.commit()
    assert client.get(path, headers=auth['admin']).json()['content'] == ''
    for sequence, content in enumerate(['instance-one stdout\n', 'instance-one stderr\n']):
        response = client.post(upload, headers=token, json={'sequence': sequence, 'content': content, 'stream': 'stderr' if sequence else 'stdout'})
        assert response.status_code == 200, response.text
    actual = client.get(path, headers=auth['admin']).json()
    assert actual['content'] == 'instance-one stdout\ninstance-one stderr\n'
    assert actual['job_id'] == created['current_job_id']
    assert actual['history'][0]['job_id'] == created['current_job_id']
    assert client.get(path, headers=auth['alice']).status_code == 403
    other = client.post('/api/v1/admin/services', headers=auth['admin'], json={**body, 'port':19094}).json()
    assert client.get(path, headers=auth['admin'], params={'job_id': other['current_job_id']}).status_code == 404
    assert client.get('/api/v1/admin/services/'+other['id']+'/logs', headers=auth['admin']).json()['content'] == ''


def test_service_v2_reads_entire_retained_window_after_cleanup(env):
    main, client, auth = env
    token, slot, body = setup(env)
    item = client.post('/api/v1/admin/services', headers=auth['admin'], json=body).json()
    task = claim(env, token, slot)
    upload = '/api/v1/worker/executions/' + task['execution_id'] + '/logs-v2'
    for index in range(18):
        assert client.post(upload, headers=token, json={'sequence': index, 'content': str(index).zfill(2)+('x'*15998)}).status_code == 200
    assert client.post('/api/v1/worker/executions/'+task['execution_id']+'/complete', headers=token, json={'status':'FAILED','error':'fixture stopped'}).status_code == 200
    data = client.get('/api/v1/admin/services/'+item['id']+'/logs', headers=auth['admin']).json()
    assert data['content'].startswith('02')
    assert len(data['content'].encode()) == 256000
    assert '17' in data['content']
