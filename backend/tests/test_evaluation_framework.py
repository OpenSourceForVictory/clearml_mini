import json
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import template, batch_body, prepare
from backend.app.core import ClientTemplate, Job, Run, Slot
from backend.app.scheduling import tick, transition
from runner.sdk import EvaluationClient, EpisodeResult, run, EVENT_PREFIX


def test_suite_versions_ownership_and_backlog(env):
    _, c, auth = env
    t = template(env)
    body = batch_body(t)
    definition = {'name': 'robot', 'version': '1', 'template_id': t, 'cases': body['cases']}
    saved = c.post('/api/v1/suites', headers=auth['alice'], json=definition)
    assert saved.status_code == 201, saved.text
    assert c.post('/api/v1/suites', headers=auth['alice'], json=definition).status_code == 409
    request = {**body, 'suite_id': saved.json()['id'], 'cases': [], 'max_concurrent': 2}
    first = c.post('/api/v1/batches', headers=auth['alice'], json=request)
    second = c.post('/api/v1/batches', headers=auth['alice'], json=request)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] != second.json()['id']
    assert first.json()['max_concurrent'] == 2
    assert c.post('/api/v1/batches', headers=auth['bob'], json=request).status_code == 404
    assert c.get('/api/v1/suites', headers=auth['bob']).json() == []
    large = {**body, 'cases': [{**body['cases'][0], 'case_id': str(i)} for i in range(30)]}
    assert c.post('/api/v1/batches', headers=auth['alice'], json=large).status_code == 201


def test_batch_concurrency_and_round_robin(env, monkeypatch):
    main, c, auth = env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '10'); monkeypatch.setenv('MAX_ACTIVE_PER_USER', '10')
    t = template(env); _, slots = node(env, count=3)
    with main.SessionLocal() as s:
        for sid in slots:
            slot = s.get(Slot, sid); slot.protocol_version = 3; slot.client_images = ['approved:cs']
        s.commit()
    a = c.post('/api/v1/batches', headers=auth['alice'], json=batch_body(t)).json()
    b = c.post('/api/v1/batches', headers=auth['alice'], json=batch_body(t)).json()
    assert tick(main.SessionLocal) == 2  # each batch max_concurrent=1, third slot stays free
    with main.SessionLocal() as s:
        active = s.scalars(select(Run).where(Run.status == 'RESERVED')).all()
        assert {s.get(Job, r.job_id).batch_id for r in active} == {a['id'], b['id']}
        for r in active:
            transition(s, r, 'FAILED'); r.cleanup_confirmed = True
        s.commit()
    assert tick(main.SessionLocal) == 2


def test_episode_progress_idempotence_completion_and_attempt_isolation(env):
    main, c, auth = env
    _, token, _, jid, task = prepare(env)
    with main.SessionLocal() as s:
        j = s.get(Job, jid)
        j.client_spec = {**j.client_spec, 'template': {**j.client_spec['template'], 'episode_reporting': True}}
        s.commit()
    base = '/api/v1/worker/executions/'+task['execution_id']
    view = f'/api/v1/jobs/{jid}/attempts/{task["run_id"]}/episodes'
    final = {'status': 'SUCCEEDED', 'metrics': {'success_rate': .5, 'episode_count': 2, 'runtime': 1}}
    assert c.post(base+'/complete', headers=token, json=final).status_code == 400
    for i in (1, 2):
        start = {'episode_id': i, 'seed': 6+i, 'status': 'STARTED'}
        end = {**start, 'status': 'COMPLETED', 'success': i == 1, 'metrics': {'reward': float(i)}, 'runtime': .1, 'artifacts': []}
        assert c.post(base+'/episodes', headers=token, json=end).status_code == 409
        for _ in range(2):
            assert c.post(base+'/episodes', headers=token, json=start).status_code == 200
            assert c.post(base+'/episodes', headers=token, json=end).status_code == 200
        assert c.post(base+'/episodes', headers=token, json={**end, 'success': i != 1}).status_code == 409
    progress = c.get(view, headers=auth['alice']).json()['progress']
    assert progress['completed'] == 2 and progress['successes'] == 1 and progress['success_rate'] == .5
    assert c.get(view, headers=auth['bob']).status_code == 404
    assert c.post(base+'/complete', headers=token, json={**final, 'metrics': {**final['metrics'], 'success_rate': 1}}).status_code == 400
    assert c.post(base+'/complete', headers=token, json={'status': 'FAILED', 'error': 'after results'}).status_code == 200
    assert c.post('/api/v1/jobs/'+jid+'/retry', headers=auth['alice']).status_code == 202
    detail = c.get('/api/v1/jobs/'+jid, headers=auth['alice']).json()
    newrun = detail['attempts'][-1]['id']
    fresh = c.get(f'/api/v1/jobs/{jid}/attempts/{newrun}/episodes', headers=auth['alice']).json()
    assert fresh['progress']['completed'] == 0
    assert c.get(view, headers=auth['alice']).json()['progress']['completed'] == 2


def test_sdk_goal_failure_and_exception_cleanup(tmp_path, capsys):
    class Client(EvaluationClient):
        closed = False
        def run_episode(self, task, episode, context):
            assert episode.seed == 40 + episode.id
            return EpisodeResult(success=episode.id == 1, metrics={'value': 1})
        def teardown(self): self.closed = True
    client = Client()
    result = run(client, {'seed': 41, 'episodes': 2}, tmp_path)
    assert result['success_rate'] == .5 and client.closed
    events = [json.loads(x[len(EVENT_PREFIX):]) for x in capsys.readouterr().out.splitlines() if x.startswith(EVENT_PREFIX)]
    assert [e['status'] for e in events] == ['STARTED', 'COMPLETED', 'STARTED', 'COMPLETED']
    class Broken(Client):
        def run_episode(self, *args): raise RuntimeError('simulation crashed')
    broken = Broken()
    with pytest.raises(RuntimeError): run(broken, {'seed': 41, 'episodes': 2}, tmp_path)
    assert broken.closed
    assert '"status":"ERROR"' in capsys.readouterr().out


def test_worker_episode_cursor_retries_after_lost_response(tmp_path):
    from worker.client import Worker
    worker = Worker({}, tmp_path/'journal')
    worker.state = {}
    event = {'episode_id': 1, 'seed': 7, 'status': 'STARTED'}
    output = EVENT_PREFIX+json.dumps(event)+'\n'
    calls = []
    class API:
        def post(self, path, body):
            calls.append(body)
            if len(calls) == 1: raise TimeoutError()
    worker.api = API()
    with pytest.raises(TimeoutError): worker.send_episodes('execution', output)
    worker.send_episodes('execution', output)
    worker.send_episodes('execution', output)
    assert calls == [event, event]


def test_source_build_permissions_validation_and_publication(env):
    _, c, auth = env
    source = {'name': 'my simulator', 'version': '1', 'entrypoint': 'client:Client',
              'files': {'client.py': 'from runner.sdk import EvaluationClient\nclass Client(EvaluationClient): pass\n'}}
    for path in ['../escape.py', '/absolute.py', 'runner/sdk.py', 'Dockerfile']:
        assert c.post('/api/v1/client-builds', headers=auth['alice'], json={**source, 'files': {path: 'x'}}).status_code == 400
    response = c.post('/api/v1/client-builds', headers=auth['alice'], json=source)
    assert response.status_code == 201, response.text
    assert c.get('/api/v1/client-builds', headers=auth['bob']).json() == []
    assert c.post('/api/v1/admin/client-builds/claim', headers=auth['alice'], json={}).status_code == 403
    build = c.post('/api/v1/admin/client-builds/claim', headers=auth['admin'], json={}).json()['build']
    assert build['source']['files'] == source['files']
    base = '/api/v1/admin/client-builds/'+build['id']+'/complete'
    result = {'token': build['token'], 'status': 'SUCCEEDED', 'image': 'tested:client', 'log': 'built'}
    assert c.post(base, headers=auth['admin'], json={**result, 'token': 'wrong'}).status_code == 404
    published = c.post(base, headers=auth['admin'], json=result)
    assert published.status_code == 200 and published.json()['template_id']
    assert c.post(base, headers=auth['admin'], json=result).json()['template_id'] == published.json()['template_id']
    assert c.post('/api/v1/client-builds', headers=auth['alice'], json={**source, 'version': '2'}).status_code == 201
    failed = c.post('/api/v1/admin/client-builds/claim', headers=auth['admin'], json={}).json()['build']
    r = c.post('/api/v1/admin/client-builds/'+failed['id']+'/complete', headers=auth['admin'],
               json={'token': failed['token'], 'status': 'FAILED', 'log': 'dependency error'})
    assert r.status_code == 200 and r.json()['template_id'] is None


def test_sdk_module_entrypoint_uses_canonical_classes(tmp_path):
    import os
    import subprocess
    import sys
    source = tmp_path/'client.py'
    source.write_text('from runner.sdk import EvaluationClient, EpisodeResult\nclass Client(EvaluationClient):\n def run_episode(self, task, episode, context): return EpisodeResult(success=False)\n')
    config = tmp_path/'input.json'; config.write_text(json.dumps({'episodes': 2, 'seed': 42}))
    result = subprocess.run([sys.executable, '-m', 'runner.sdk', 'client:Client'], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': str(tmp_path)+os.pathsep+os.getcwd(), 'MINI_EVAL_INPUT': str(config), 'MINI_EVAL_OUTPUT_DIR': str(tmp_path/'output')}, timeout=10)
    assert result.returncode == 0, result.stderr
    assert '"episode_count": 2' in result.stdout


def test_workspace_isolation_paths_and_event_file(tmp_path):
    import uuid
    from worker.workspace import prepare, execution_dirs, read_events
    root = prepare({'workspace_root': str(tmp_path/'workspace')}, '/unused')
    a = execution_dirs(root, str(uuid.uuid4())); b = execution_dirs(root, str(uuid.uuid4()))
    assert a != b and a.parent == b.parent
    event = a/'work'/'episodes.jsonl'
    event.write_bytes(b'hello\nworld\n')
    assert read_events(event, 6) == b'world\n'
    assert execution_dirs(root, a.name) == a
    with pytest.raises(ValueError): prepare({'workspace_root': 'relative'}, '/unused')
    with pytest.raises(ValueError): execution_dirs(root, '../escape')
    linked = b/'work'/'episodes.jsonl'; linked.symlink_to(event)
    with pytest.raises(OSError): read_events(linked, 0)
    with pytest.raises(RuntimeError): read_events(event, 999)


def test_workspace_disk_admission_recovery_and_stale_telemetry(env):
    from datetime import timedelta
    from backend.app.core import now
    from backend.tests.test_execution import submit
    main, c, _ = env
    token, slots = node(env)
    submit(env)
    assert c.post('/api/v1/worker/heartbeat', headers=token,
                  json={'slot_id': slots[0], 'disk': {'free_mb': 100, 'min_free_mb': 2048}}).status_code == 200
    assert tick(main.SessionLocal) == 0
    c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0], 'disk': {'free_mb': 8192, 'min_free_mb': 2048}})
    with main.SessionLocal() as s:
        s.get(Slot, slots[0]).disk_at = now()-timedelta(seconds=20); s.commit()
    assert tick(main.SessionLocal) == 0
    c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0], 'disk': {'free_mb': 8192, 'min_free_mb': 2048}})
    assert tick(main.SessionLocal) == 1
