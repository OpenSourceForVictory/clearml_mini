import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import pytest

from backend.tests.test_command_tasks import definition
from backend.tests.test_execution import env, node
from backend.app.core import Slot, now
from runner.sdk import report_task_result
from runner.task_result_contract import validate
from worker.client import Worker


def test_sdk_report_is_atomic_single_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv('MINI_EVAL_WORK_DIR', str(tmp_path))
    expected = {'success': True, 'metrics': {'score': .75}, 'summary': 'done'}
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(lambda _: report_task_result(True, {'score': .75}, 'done'), range(6)))
    assert values == [expected] * 6
    assert json.loads((tmp_path/'task-result.json').read_text()) == expected
    with pytest.raises(RuntimeError, match='different task result'):
        report_task_result(False)


@pytest.mark.parametrize('value', [
    {'success': 1}, {'success': True, 'metrics': {'score': True}},
    {'success': True, 'metrics': {'score': float('nan')}},
    {'success': True, 'summary': 'x' * 501},
    {'success': True, 'unexpected': 1},
])
def test_task_result_contract_rejects_invalid_values(value):
    with pytest.raises((ValueError, TypeError)):
        validate(value)


def test_sdk_requires_execution_work_directory(monkeypatch):
    monkeypatch.delenv('MINI_EVAL_WORK_DIR', raising=False)
    with pytest.raises(RuntimeError, match='MINI_EVAL_WORK_DIR'):
        report_task_result(True)


def test_worker_forwards_execution_scoped_result_and_retries(tmp_path):
    class API:
        def __init__(self):
            self.calls = []
            self.fail = True
        def post(self, path, body):
            if self.fail:
                self.fail = False
                raise TimeoutError('response unavailable')
            self.calls.append((path, body))
            return {}

    execution = '01a18543-4fe2-7d23-85d4-2fe176e924a7'
    workspace = tmp_path/'workspace'
    work = workspace/'runs'/execution/'work'
    work.mkdir(parents=True)
    (work/'task-result.json').write_text(json.dumps({'success': False, 'metrics': {'score': .2}}))
    worker = Worker.__new__(Worker)
    worker.state = {'task': {'client_spec': {'template': {'integration_mode': 'sdk-task-result'}}}}
    worker.workspace = workspace
    worker.path = tmp_path/'state.json'
    worker.api = API()
    with pytest.raises(TimeoutError):
        worker.send_task_result(execution)
    assert 'task_result_sha256' not in worker.state
    assert worker.send_task_result(execution)
    assert worker.api.calls == [(f'worker/executions/{execution}/task-result', {'success': False, 'metrics': {'score': .2}})]
    assert worker.send_task_result(execution)
    assert len(worker.api.calls) == 1
    recovered = Worker.__new__(Worker)
    recovered.state = json.loads(worker.path.read_text())
    recovered.workspace = workspace
    recovered.path = worker.path
    recovered.api = API()
    recovered.api.fail = False
    assert recovered.send_task_result(execution)
    assert recovered.api.calls == []

    other = '99a18543-4fe2-7d23-85d4-2fe176e924a7'
    other_work = workspace/'runs'/other/'work'
    other_work.mkdir(parents=True)
    (other_work/'task-result.json').write_text(json.dumps({'success': True, 'metrics': {'score': .9}}))
    second = Worker.__new__(Worker)
    second.state = {'task': {'client_spec': {'template': {'integration_mode': 'sdk-task-result'}}}}
    second.workspace = workspace
    second.path = tmp_path/'state-2.json'
    second.api = API()
    second.api.fail = False
    assert second.send_task_result(other)
    assert second.api.calls[0][0] == f'worker/executions/{other}/task-result'
    assert second.api.calls[0][1]['success'] is True


def test_task_result_api_auth_idempotency_conflict_and_exit_code_separation(env):
    main, client, auth = env
    template = {**definition(), 'integration_mode': 'sdk-task-result'}
    created = client.post('/api/v1/admin/client-templates', headers=auth['admin'], json=template)
    assert created.status_code == 201, created.text
    submitted = client.post('/api/v1/jobs', headers=auth['alice'], json={
        'title': 'single task result', 'benchmark': 'cs-client', 'model_ref': 'command',
        'episodes': 1, 'pool_id': 'mock', 'client': {'template_id': created.json()['id'],
        'service_mode': 'none', 'task_name': 'single-result'}})
    assert submitted.status_code == 201, submitted.text
    token, slots = node(env)
    with main.SessionLocal() as session:
        slot = session.get(Slot, slots[0])
        slot.protocol_version = 17
        slot.client_images = ['approved:cs']
        slot.storage_report = {'revision': 0, 'status': 'applied'}
        slot.storage_reported_at = now()
        slot.runtime_report = {'revision': 0, 'applied_revision': 0,
                               'images': {'approved:cs': {'ready': True}}, 'assets': {}}
        slot.runtime_reported_at = now()
        session.commit()
    from backend.app.scheduling import tick
    tick(main.SessionLocal)
    old_worker = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token, json={})
    assert old_worker.status_code == 200 and old_worker.json()['task'] is None
    with main.SessionLocal() as session:
        session.get(Slot, slots[0]).protocol_version = 18
        session.commit()
    tick(main.SessionLocal)
    claim = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token, json={})
    assert claim.status_code == 200, claim.text
    execution = claim.json()['task']['execution_id']
    path = f'/api/v1/worker/executions/{execution}/task-result'
    payload = {'success': False, 'metrics': {'score': .25}, 'summary': 'business rejected'}
    assert client.post(path, headers=auth['alice'], json=payload).status_code == 401
    assert client.post(path, headers=token, json=payload).status_code == 200
    duplicate = client.post(path, headers=token, json=payload)
    assert duplicate.status_code == 200 and duplicate.json()['duplicate']
    assert client.post(path, headers=token, json={**payload, 'success': True}).status_code == 409
    assert client.post(path, headers=token, json={'success': True, 'metrics': {'score': 'high'}}).status_code == 400
    assert client.post(f'/api/v1/worker/executions/{execution}/complete', headers=token,
                       json={'status': 'SUCCEEDED', 'exit_code': 0}).status_code == 200
    detail = client.get('/api/v1/jobs/'+submitted.json()['id'], headers=auth['alice']).json()
    assert detail['status'] == 'SUCCEEDED'
    assert detail['attempts'][0]['metrics'] == {'kind': 'task', **payload}


def test_task_result_template_mode_is_opt_in_and_restricted(env):
    _, client, auth = env
    body = {**definition(), 'integration_mode': 'sdk-task-result'}
    created = client.post('/api/v1/admin/client-templates', headers=auth['admin'], json=body)
    assert created.status_code == 201 and created.json()['integration_mode'] == 'sdk-task-result'
    for changes in ({'task_type': 'evaluation'}, {'episode_reporting': True}):
        response = client.post('/api/v1/admin/client-templates', headers=auth['admin'],
                               json={**definition(), 'integration_mode': 'sdk-task-result', **changes})
        assert response.status_code == 400


def test_docker_runtime_environment_exposes_the_task_result_channel(tmp_path):
    from runner.image_runtime import resolve
    work=tmp_path/'work';work.mkdir()
    output=tmp_path/'output';output.mkdir()
    script=Path(__file__).resolve().parents[2]/'examples/integration/task_result_client.py'
    runtime={'interpreter':os.sys.executable,'work_dir':str(work),'output_dir':str(output),
             'working_directory':str(tmp_path),'command_mode':'exec'}
    command, additions, cwd=resolve({'task_name':'example','episodes':1,'seed':0,'endpoint':'',
        'template':{'command':[os.sys.executable,str(script)],'runtime':runtime},'environment':{}})
    env={**os.environ,**additions,'PYTHONPATH':str(Path(__file__).resolve().parents[2]),
         'MINI_EVAL_INPUT':str(tmp_path/'input.json')}
    (tmp_path/'input.json').write_text(json.dumps({'business':{'demo_pass':True}}))
    result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert json.loads((work/'task-result.json').read_text())['success'] is True
