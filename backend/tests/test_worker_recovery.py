import uuid
import pytest
from worker.client import APIError, Worker


@pytest.mark.parametrize('runtime', ['docker', 'nvidia'])
def test_render_check_keeps_gpu_and_container_isolation(tmp_path, monkeypatch, runtime):
    w = Worker({'runner_image': 'approved:render', 'gpu_runtime': runtime}, tmp_path/'state')
    w.slot_id = 'slot'
    task = {'benchmark': 'robotwin-render', 'image': 'approved:render', 'gpu_uuid': 'GPU-selected',
            'execution_id': str(uuid.uuid4()), 'job_id': 'job', 'model_ref': 'sapien-render',
            'cpus': 2, 'ram_gb': 4, 'timeout_seconds': 120, 'episodes': 2}
    calls = []
    monkeypatch.setattr('worker.client.docker', lambda *args, **kwargs: calls.append(args))
    w.create(task)
    args = calls.pop()
    assert args[args.index('--network')+1] == 'none'
    assert args[args.index('--cap-drop')+1] == 'ALL'
    assert '--privileged' not in args and '--volume' not in args
    assert 'runner.robotwin_render' in args
    if runtime == 'nvidia':
        assert 'NVIDIA_VISIBLE_DEVICES=GPU-selected' in args
    else:
        assert args[args.index('--gpus')+1] == 'device=GPU-selected'
    for invalid in ({'gpu_uuid': None}, {'image': 'unapproved'}, {'benchmark': 'arbitrary'},
                    {'model_ref': '/tmp/model'}, {'episodes': 101}):
        with pytest.raises(RuntimeError):
            w.create({**task, **invalid})
    assert calls == []


def test_expired_journal_never_starts_container(tmp_path, monkeypatch):
    w = Worker({'api_url': 'http://unused', 'token': 'unused'}, tmp_path/'state')
    w.slot_id = 'slot'
    execution = str(uuid.uuid4())
    task = {'execution_id': execution, 'lease_seconds': 30, 'status': 'STARTING'}
    calls = []
    class API:
        def post(self, path, data=None):
            calls.append(path)
            if path == 'worker/heartbeat':
                raise APIError(409, 'expired')
            return {}
    w.api = API()
    monkeypatch.setattr(w, 'inspect', lambda _: None)
    monkeypatch.setattr(w, 'create', lambda _: (_ for _ in ()).throw(AssertionError('must not create')))
    w.execute(task)
    assert calls == ['worker/heartbeat', f'worker/executions/{execution}/cleanup']
    assert w.state is None


def test_log_retry_preserves_exact_pending_chunk(tmp_path, monkeypatch):
    w = Worker({'api_url': 'http://unused', 'token': 'unused'}, tmp_path/'state')
    w.state = {'offset': 0, 'sequence': 0, 'pending': None}
    bodies = []
    class API:
        def post(self, path, data=None):
            bodies.append(dict(data))
            if len(bodies) == 1:
                raise TimeoutError('response lost after commit')
            return {}
    w.api = API()
    try:
        w.send_logs('execution', 'first')
    except TimeoutError:
        pass
    w.send_logs('execution', 'first plus later output')
    assert bodies[0] == bodies[1] == {'sequence': 0, 'content': 'first'}
    assert bodies[2]['sequence'] == 1
    assert bodies[2]['content'] == ' plus later output'


def test_multiple_journals_migrate_and_remain_independent(tmp_path):
    parent = Worker({}, tmp_path/'state')
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    parent.state = {'task': {'execution_id': first}, 'offset': 3, 'sequence': 2}
    parent.save()
    child = parent.child(second)
    child.state = {'task': {'execution_id': second}, 'offset': 9, 'sequence': 7}
    child.save()
    parent.recover_journals()
    assert parent.state is None
    assert set(parent.children) == {first, second}
    recovered = Worker({}, tmp_path/'state')
    recovered.recover_journals()
    assert recovered.children[first].state['sequence'] == 2
    assert recovered.children[second].state['sequence'] == 7
    recovered.children[first].state['sequence'] = 3
    recovered.children[first].save()
    assert recovered.child(second).state['sequence'] == 7


def test_start_rechecks_actual_memory(tmp_path, monkeypatch):
    w = Worker({'runner_image': 'approved'}, tmp_path/'state')
    task = {'benchmark': 'mock', 'image': 'approved', 'requested_vram_mb': 8192, 'vram_reserve_mb': 2048}
    monkeypatch.setattr('worker.client.telemetry', lambda _: {'free_vram_mb': 9000, 'gpu_utilization': 0})
    monkeypatch.setattr('worker.client.docker', lambda *a, **kw: pytest.fail('must not create container'))
    with pytest.raises(RuntimeError, match='insufficient free GPU memory'):
        w.create(task)


def test_parallel_tasks_keep_original_execution_after_restart(tmp_path, monkeypatch):
    import threading
    executions = [str(uuid.uuid4()), str(uuid.uuid4())]
    parent = Worker({}, tmp_path/'state')
    parent.slot_id = 'slot'
    barrier = threading.Barrier(2)
    completed = []
    class API:
        def post(self, path, data=None):
            if path.endswith('/heartbeat'):
                barrier.wait(timeout=5)
                return {'lease_seconds': 30, 'cancel': False}
            if path.endswith('/complete'):
                completed.append(path.split('/')[-2])
            return {}
    for execution in executions:
        child = parent.child(execution)
        child.api = API()
        task = {'execution_id': execution, 'lease_seconds': 30, 'status': 'RUNNING'}
        child.state = {'task': task, 'offset': 0, 'sequence': 0, 'created': True, 'pending': None}
        child.save()
        monkeypatch.setattr(child, 'inspect', lambda _: {'State': {'Running': False, 'Status': 'exited', 'ExitCode': 0}})
        monkeypatch.setattr(child, 'create', lambda _: pytest.fail('must not recreate'))
        monkeypatch.setattr(child, 'remove', lambda _: None)
        monkeypatch.setattr(child, 'stop_container', lambda _: None)
        parent.children[execution] = child
    class Result:
        stdout = '{"final":{"success_rate":1,"episode_count":1,"runtime":0.1}}'
        stderr = ''
    monkeypatch.setattr('worker.client.docker', lambda *a, **kw: Result())
    for child in list(parent.children.values()):
        parent.launch(child, child.state['task'])
    for thread in parent.threads.values():
        thread.join(timeout=8)
        assert not thread.is_alive()
    assert set(completed) == set(executions)
    assert all(c.state is None for c in parent.children.values())
