"""First failure survives outages without recreating uncertain executions."""
import json
import uuid
from types import SimpleNamespace

import pytest
from worker.client import APIError, Worker
from worker.host_backend import HostBackend
from backend.tests.test_worker_execution_recovery import execution


@pytest.mark.parametrize('outage', ['503', 'lost-response', 'cleanup'])
@pytest.mark.parametrize('mode', ['host', 'docker'])
def test_first_failure_survives_reload(execution, monkeypatch, outage, mode):
    worker, task, events = execution
    task.update(status='STARTING', client_spec={'template': {'execution_mode': mode}})
    monkeypatch.setattr(worker, 'inspect', lambda e: None)
    def create(task):
        raise RuntimeError('host launch failed: original systemd error')
    monkeypatch.setattr(worker, 'create', create)
    if outage == 'cleanup':
        def stop(e):
            raise RuntimeError('cleanup unavailable')
        monkeypatch.setattr(worker, 'stop_container', stop)
    original = worker.api.post
    def post(path, body=None):
        if path.endswith('/complete'):
            raise APIError(503, 'busy') if outage == '503' else TimeoutError('response lost')
        return original(path, body)
    worker.api.post = post
    with pytest.raises((APIError, TimeoutError, RuntimeError)):
        worker.execute(task)
    saved = json.loads(worker.path.read_text())
    assert saved['terminal_failure']['error'] == 'host launch failed: original systemd error'
    recovered = Worker(worker.config, worker.path, snapshot=worker.storage_snapshot)
    recovered.slot_id = worker.slot_id
    monkeypatch.setattr(recovered, 'create', lambda t: pytest.fail('duplicate creation'))
    monkeypatch.setattr(recovered, 'inspect', lambda e: pytest.fail('replay inspected for creation'))
    monkeypatch.setattr(recovered, 'stop_container', lambda e: events.append(('stop', e)))
    monkeypatch.setattr(recovered, 'remove', lambda e: events.append(('remove', e)))
    def replay(path, body=None):
        if path.endswith('/complete'):
            assert body == saved['terminal_failure']
            if outage == 'lost-response':
                raise APIError(409, 'already completed')
        return original(path, body)
    recovered.api.post = replay
    recovered.execute(task)
    assert recovered.state is None
    assert json.loads(worker.path.read_text()) is None
    if outage == 'lost-response':
        assert any(path.endswith('/cleanup') for path, body in events)


@pytest.mark.parametrize('created,status,source', [(True, 'STARTING', 'journal-created'), (False, 'RUNNING', 'control-plane-running')])
def test_old_missing_journal_reports_identity(execution, monkeypatch, created, status, source):
    worker, task, events = execution
    task.update(status=status, service_instance_id=str(uuid.uuid4()), client_spec={'template': {'execution_mode': 'host'}})
    worker.state = {'task': task, 'created': created}
    monkeypatch.setattr(worker, 'inspect', lambda e: None)
    monkeypatch.setattr(worker, 'create', lambda t: pytest.fail('duplicate creation'))
    worker.execute(task)
    error = next(body['error'] for path, body in events if path.endswith('/complete'))
    assert 'source='+source in error
    assert task['execution_id'] in error and task['service_instance_id'] in error


@pytest.mark.parametrize('system', [False, True])
def test_two_card_workers_keep_independent_handles(tmp_path, monkeypatch, system):
    monkeypatch.setattr('worker.host_backend.os.geteuid', lambda: 1000)
    children = []
    for index in (0, 1):
        config = {'gpu_index': index, 'host_execution': {'approved': True} if system else {}}
        # Even a shared workspace must not merge execution control records.
        snapshot = {'revision': 0, 'config': {'workspace_root': str(tmp_path/'work')}}
        parent = Worker(config, tmp_path/f'gpu{index}.json', snapshot=snapshot)
        child = parent.child(str(uuid.uuid4()))
        child.state = {'created': True, 'task': {'execution_id': child.path.stem, 'name': 'same service'}}
        child.save()
        host = HostBackend(child)
        host.root.mkdir(parents=True)
        (host.root/'launch.json').write_text(json.dumps({'execution_id': child.path.stem, 'started': False}))
        monkeypatch.setattr(host, 'control', lambda *a, **kw: SimpleNamespace(stdout='LoadState=not-found\n', returncode=0))
        children.append((child, host))
    (first, a), (second, b) = children
    assert first.path != second.path and a.root != b.root
    assert a.unit(first.path.stem) != b.unit(second.path.stem)
    a.remove(first.path.stem)
    assert b.inspect(second.path.stem)['State']['Status'] == 'created'
    assert json.loads(second.path.read_text())['created'] is True


def test_failed_exit_code_is_persisted_before_completion(execution, monkeypatch):
    worker, task, events = execution
    monkeypatch.setattr(worker, 'inspect', lambda e: {'State': {'Status': 'exited', 'Running': False, 'ExitCode': 7}})
    original = worker.api.post
    def post(path, body=None):
        if path.endswith('/complete'):
            raise APIError(503, 'busy')
        return original(path, body)
    worker.api.post = post
    with pytest.raises(APIError):
        worker.execute(task)
    failure = json.loads(worker.path.read_text())['terminal_failure']
    assert failure['status'] == 'FAILED' and failure['exit_code'] == 7
    assert failure['error'] == 'runner exited with code 7'
