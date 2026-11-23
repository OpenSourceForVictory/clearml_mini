"""First failure survives outages without recreating uncertain executions."""
import json
import urllib.error
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


@pytest.mark.parametrize('created,status,source', [(True, 'STARTING', 'journal-created'),
    (True, 'RUNNING', 'journal-created'), (False, 'RUNNING', 'control-plane-running')])
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


class InterruptedLaunch(BaseException):
    """Model abrupt termination without entering ordinary error handlers."""


@pytest.mark.parametrize('system', [False, True], ids=['user-manager', 'system-manager'])
@pytest.mark.parametrize('point', ['journal-only', 'prepared', 'start-marked', 'finished'])
def test_interrupted_launch_reloads_real_markers_without_duplicate_execution(
    execution, monkeypatch, tmp_path, system, point
):
    worker, task, events = execution
    monkeypatch.setattr('worker.host_backend.os.geteuid', lambda: 1000)
    worker.config['host_execution'] = {'approved': True} if system else {}
    task.update(status='STARTING', cpus=0, ram_gb=0, timeout_seconds=60,
                client_spec={'template': {'execution_mode': 'host', 'task_type': 'command'}})
    monkeypatch.setattr(worker, 'inspect', lambda eid: Worker.inspect(worker, eid))
    monkeypatch.setattr(HostBackend, 'control', lambda *a, **kw:
                        SimpleNamespace(stdout='LoadState=not-found\n', stderr='', returncode=0))
    monkeypatch.setattr('worker.host_backend.os.chown', lambda *a: None)
    monkeypatch.setattr('worker.host_identity.settings', lambda cfg: SimpleNamespace())
    monkeypatch.setattr('worker.host_identity.properties', lambda account: [])
    monkeypatch.setattr('worker.resource_limits.verify_group', lambda *a: None)
    launches = []

    def create(_):
        # Assert the real execution journal is durable before creation begins.
        assert json.loads(worker.path.read_text())['created'] is True
        if point == 'journal-only':
            raise InterruptedLaunch()
        host = HostBackend(worker)
        host.root.mkdir(parents=True, exist_ok=True)
        (host.root/'launch.json').write_text(json.dumps({
            'execution_id': task['execution_id'], 'started': False}))
        if point == 'prepared':
            raise InterruptedLaunch()

    def interrupted_systemd(argv, **kwargs):
        assert argv[0] == 'systemd-run'
        host = HostBackend(worker)
        assert json.loads((host.root/'launch.json').read_text())['started'] is True
        launches.append(argv)
        if point == 'finished':
            (host.root/'exit.json').write_text('{"code":0}')
        raise InterruptedLaunch()

    monkeypatch.setattr(worker, 'create', create)
    monkeypatch.setattr('worker.host_backend.subprocess.run', interrupted_systemd)
    with pytest.raises(InterruptedLaunch):
        worker.execute(task)
    saved = json.loads(worker.path.read_text())
    assert saved['created'] and 'terminal_failure' not in saved
    assert not any(p.endswith('/complete') for p, _ in events)
    before = len(launches)

    # Current defaults changed: recovery must still use the execution snapshot.
    recovered = Worker(worker.config, worker.path, snapshot={
        'revision': 99, 'config': {'workspace_root': str(tmp_path/'new-default')}})
    recovered.slot_id = worker.slot_id
    recovered.api = worker.api
    assert recovered.workspace == worker.workspace
    monkeypatch.setattr(recovered, 'create', lambda _: pytest.fail('duplicate creation'))

    def resumed_systemd(argv, **kwargs):
        launches.append(argv)
        assert point == 'prepared', 'uncertain or finished execution relaunched'
        assert ('--user' in argv) is (not system)
        assert '--unit='+HostBackend(recovered).unit(task['execution_id']) in argv
        (HostBackend(recovered).root/'exit.json').write_text('{"code":0}')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr('worker.host_backend.subprocess.run', resumed_systemd)
    recovered.execute(task)
    completions = [b for p, b in events if p.endswith('/complete')]
    assert len(completions) == 1
    result = completions[0]
    assert len(launches)-before == (1 if point == 'prepared' else 0)
    if point in ('journal-only', 'start-marked'):
        assert result['status'] == 'FAILED' and result['exit_code'] is None
        assert 'source=journal-created' in result['error']
        assert 'service_instance_id=unknown' in result['error']
        assert 'unit=mini-eval-exec-'+task['execution_id']+'.service' in result['error']
    else:
        assert result['status'] == 'SUCCEEDED' and result['exit_code'] == 0
    assert recovered.state is None and json.loads(worker.path.read_text()) is None


@pytest.mark.parametrize('system', [False, True])
@pytest.mark.parametrize('failure', ['manager-unreachable', 'access-denied'])
def test_manager_errors_do_not_become_missing_unit(tmp_path, monkeypatch, system, failure):
    worker = SimpleNamespace(config={}, state={'task': {'execution_id': str(uuid.uuid4())}},
                             path=tmp_path/'state', workspace=tmp_path/'workspace')
    host = HostBackend(worker)
    host.system = system
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout='', stderr=failure)
    monkeypatch.setattr('worker.host_backend.subprocess.run', run)
    with pytest.raises(RuntimeError, match='cannot inspect host unit: '+failure):
        host.inspect(host.execution)
    assert len(calls) == 1
    assert calls[0][:2] == (['systemctl', 'show'] if system else ['systemctl', '--user'])


@pytest.mark.parametrize('stage', ['create', 'start'])
@pytest.mark.parametrize('fault', [PermissionError('business directory denied'),
                                 OSError('storage unavailable'),
                                 TimeoutError('local startup timed out')])
def test_local_startup_oserror_keeps_first_cause_across_reload(execution, monkeypatch, stage, fault):
    worker, task, events = execution
    task.update(status='STARTING', client_spec={'template': {'execution_mode': 'host'}})
    states = iter([None, {'State': {'Status': 'created', 'Running': False}}])
    monkeypatch.setattr(worker, 'inspect', lambda _: next(states))
    def fail(*args):
        raise fault
    if stage == 'create':
        monkeypatch.setattr(worker, 'create', fail)
    else:
        monkeypatch.setattr(HostBackend, 'start', fail)
    original = worker.api.post
    def unavailable(path, body=None):
        if path.endswith('/complete'):
            raise APIError(503, 'control plane unavailable')
        return original(path, body)
    worker.api.post = unavailable
    with pytest.raises((APIError, OSError)):
        worker.execute(task)
    saved = json.loads(worker.path.read_text())
    assert saved['created'] is True
    assert str(fault) in saved['terminal_failure']['error']
    recovered = Worker(worker.config, worker.path, snapshot=worker.storage_snapshot)
    recovered.slot_id = worker.slot_id
    recovered.api.post = original
    monkeypatch.setattr(recovered, 'inspect', lambda _: pytest.fail('must replay first failure'))
    monkeypatch.setattr(recovered, 'create', lambda _: pytest.fail('duplicate creation'))
    monkeypatch.setattr(recovered, 'stop_container', lambda _: None)
    monkeypatch.setattr(recovered, 'remove', lambda _: None)
    recovered.execute(task)
    completion = next(b for p, b in events if p.endswith('/complete'))
    assert completion == saved['terminal_failure']
    assert 'previously created' not in completion['error']
    assert recovered.state is None


@pytest.mark.parametrize('fault', [TimeoutError('heartbeat timeout'),
                                 urllib.error.URLError('heartbeat unavailable')])
def test_host_control_plane_transport_failure_is_still_retryable(execution, monkeypatch, fault):
    worker, task, events = execution
    task['client_spec'] = {'template': {'execution_mode': 'host'}}
    def offline(*args):
        raise fault
    worker.api.post = offline
    monkeypatch.setattr(worker, 'create', lambda _: pytest.fail('heartbeat failed before creation'))
    with pytest.raises(type(fault)):
        worker.execute(task)
    saved = json.loads(worker.path.read_text())
    assert saved['created'] is False and 'terminal_failure' not in saved
    assert not any(p.endswith('/complete') for p, _ in events)


def test_docker_creation_oserror_keeps_existing_recovery_policy(execution, monkeypatch):
    worker, task, events = execution
    task['client_spec'] = {'template': {'execution_mode': 'docker'}}
    monkeypatch.setattr(worker, 'inspect', lambda _: None)
    def unavailable(_):
        raise OSError('Docker transport unavailable')
    monkeypatch.setattr(worker, 'create', unavailable)
    with pytest.raises(OSError, match='Docker transport unavailable'):
        worker.execute(task)
    saved = json.loads(worker.path.read_text())
    assert saved['created'] is True and 'terminal_failure' not in saved
    assert not any(p.endswith('/complete') for p, _ in events)
