"""Lifecycle fault injection, with host/Docker calls replaced at their boundaries."""
from types import SimpleNamespace

import pytest

from backend.tests.test_worker_execution_recovery import execution
from worker import client
from worker.host_backend import HostBackend


@pytest.mark.parametrize('host', [False, True])
@pytest.mark.parametrize('stage', ['logs', 'episodes', 'artifacts', 'complete', 'service-health'])
@pytest.mark.parametrize('failure', ['busy', 'lost-response'])
def test_report_overload_returns_to_renewal_without_stopping(execution, monkeypatch, host, stage, failure):
    worker, task, events = execution
    task.update(benchmark='cs-client', client_spec={'template': {'execution_mode': 'host' if host else 'docker'}, 'service_mode': 'none'})
    output = 'MINI_EVAL_EVENT {"episode_id":1,"status":"STARTED"}\n'
    output += '{"final":{"success_rate":1,"episode_count":1,"runtime":0.1}}\n'
    monkeypatch.setattr(client.time, 'sleep', lambda delay: None)
    monkeypatch.setattr(client, 'docker', lambda *a, **kw: SimpleNamespace(stdout=output, stderr=''))
    monkeypatch.setattr(HostBackend, 'logs', lambda self: (output, ''))
    monkeypatch.setattr(HostBackend, 'artifacts', lambda self, execution: [('result.json', b'{}')])
    monkeypatch.setattr(client, 'read_artifacts', lambda name: [('result.json', b'{}')])
    if stage == 'service-health':
        task.update(benchmark='inference-service', client_spec={'template': {'execution_mode': 'host' if host else 'docker'}, 'service': {'startup_timeout': 60}})
        monkeypatch.setattr(client, 'service_health', lambda spec: {'ready': True})
    calls = []
    heartbeats = []
    original = worker.api.post
    def post(path, body=None):
        if path == 'worker/heartbeat':
            heartbeats.append(len(calls))
        if path.endswith('/' + stage):
            calls.append(dict(body))
            if len(calls) == 1:
                if failure == 'busy':
                    raise client.APIError(503, 'overloaded', '0')
                raise TimeoutError('committed response was lost')
            assert not any(p == 'stop' for p, b in events)
            assert 1 in heartbeats, 'must renew between failed report and retry'
            assert calls[0] == calls[1], 'replay retains identity and payload'
        return original(path, body)
    worker.api.post = post
    if stage == 'service-health':
        monkeypatch.setattr(worker, 'inspect', lambda e: {'State': {'Status': 'running', 'Running': len(calls) < 2, 'ExitCode': 0}})
    worker.execute(task)
    assert len(calls) == 2
    assert worker.state is None
    assert not any(p == 'stop' for p, b in events)


@pytest.mark.parametrize('status', [400, 401, 403, 409, 422])
def test_permanent_report_error_does_not_retry_live_program(execution, status):
    worker, task, events = execution
    calls = []
    def reject(execution, output):
        calls.append(execution)
        raise client.APIError(status, 'permanent')
    worker.send_logs = reject
    if status in (401, 403):
        with pytest.raises(client.APIError):
            worker.execute(task)
    else:
        worker.execute(task)
    assert len(calls) == 1
    assert any(p == 'stop' for p, b in events)


def test_cancel_during_artifact_renewal_returns_to_completion(execution, monkeypatch):
    worker, task, events = execution
    task.update(benchmark='cs-client', client_spec={'template': {'task_type': 'command'}, 'service_mode': 'none'})
    monkeypatch.setattr(client, 'read_artifacts', lambda name: [('a.json', b'{}')])
    original = worker.api.post
    beats = []
    def post(path, body=None):
        reply = original(path, body)
        if path == 'worker/heartbeat':
            beats.append(path)
            return {**reply, 'cancel': len(beats) >= 3}
        return reply
    worker.api.post = post
    worker.execute(task)
    assert not any(path.endswith('/artifacts') for path, body in events)
    completion = [body for path, body in events if path.endswith('/complete')]
    assert len(completion) == 1 and completion[0]['status'] == 'CANCELLED'
    assert worker.state is None


def test_stop_interrupts_report_retry_and_preserves_failure_semantics(execution):
    worker, task, events = execution
    def reject(execution, output):
        worker.stop_event.set()
        raise client.APIError(503, 'busy', '600')
    worker.send_logs = reject
    worker.execute(task)
    assert any(path == 'stop' for path, body in events)
    completion = next(body for path, body in events if path.endswith('/complete'))
    assert completion['status'] == 'FAILED'
    assert 'worker stopped or lease expired' in completion['error']


def test_shared_root_registration_retries_before_completion(execution, monkeypatch):
    worker, task, events = execution
    task.update(benchmark='cs-client', artifact_transport='shared',
                client_spec={'template': {'task_type':'command'}, 'service_mode':'none'})
    original=worker.api.post
    attempts=[]
    def post(path, body=None):
        if path.endswith('/shared-artifacts'):
            attempts.append(body)
            if len(attempts)==1:raise client.APIError(503,'busy')
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    assert len(attempts)==2 and attempts[0]==attempts[1]
    assert worker.state is None
    assert not any(path=='stop' for path,body in events)


def test_health_overload_does_not_extend_service_startup_deadline(execution,monkeypatch):
    worker,task,events=execution
    task.update(benchmark='inference-service',client_spec={'template':{},'service':{'startup_timeout':0}})
    monkeypatch.setattr(worker,'inspect',lambda e:{'State':{'Status':'running','Running':True,'ExitCode':None}})
    monkeypatch.setattr(client,'service_health',lambda spec:{'ready':False})
    original=worker.api.post
    def post(path,body=None):
        if path.endswith('/service-health'):raise client.APIError(503,'busy')
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['error']=='service startup health deadline exceeded'
