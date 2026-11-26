import math
import pytest
from worker.heartbeat_cadence import HeartbeatCadence


def setup(config=None, jitter=lambda a, b: 1):
    clock = [100.]
    cadence = HeartbeatCadence(config or {}, clock=lambda: clock[0], jitter=jitter)
    body = {'slot_id': 'slot', 'software': {'version': 'v'},
            'storage': {'revision': 1, 'capacity': {'free': 100}},
            'runtime': {'revision': 1}, 'host': {'revision': 1},
            'disk': {'free_mb': 100}, 'telemetry': {'free_vram_mb': 100}}
    return clock, cadence, body


def test_separate_base_and_full_periods_preserve_resource_samples():
    clock, cadence, body = setup()
    assert cadence.prepare(body) == body
    cadence.accepted(body)
    for at in (102, 104):
        clock[0] = at
        assert cadence.prepare(body) is None
    clock[0] = 105
    body['storage']['capacity']['free'] = 90
    body['telemetry']['free_vram_mb'] = 50
    sent = cadence.prepare(body)
    assert set(sent) == {'slot_id', 'disk', 'telemetry'}
    assert sent['telemetry']['free_vram_mb'] == 50
    cadence.accepted(body)
    clock[0] = 120
    assert cadence.prepare(body) == body


@pytest.mark.parametrize('field', ['software', 'storage', 'runtime', 'host'])
def test_changed_receipt_sends_before_next_heartbeat(field):
    clock, cadence, body = setup()
    cadence.accepted(body)
    clock[0] += 1
    body[field]['changed'] = True
    assert cadence.prepare(body) == body


def test_failed_send_keeps_receipt_and_retry_payload():
    clock, cadence, body = setup()
    sent = cadence.prepare(body)
    clock[0] += 2
    assert cadence.prepare(body) == sent
    assert cadence.signature is None
    sent['host']['revision'] = 999
    assert body['host']['revision'] == 1
    cadence.accepted(body)
    clock[0] += 1
    body['telemetry_failed'] = True
    assert cadence.prepare(body) == body


@pytest.mark.parametrize('factor', [.9, 1.1])
def test_jitter_and_poll_delay(factor):
    clock, cadence, body = setup(jitter=lambda a, b: factor)
    cadence.accepted(body)
    assert cadence.next_at == 100 + 5 * factor
    assert cadence.delay() == 2
    clock[0] = cadence.next_at - .2
    assert cadence.delay() == pytest.approx(.2)
    clock[0] += 1
    assert cadence.delay() == 0


@pytest.mark.parametrize('key,value', [
    ('worker_heartbeat_seconds', x) for x in (True, '5', None, 0, 1, 11, math.nan, math.inf)
] + [('worker_report_seconds', x) for x in (False, 9, 31, math.nan)])
def test_reject_unsafe_configuration(key, value):
    with pytest.raises(ValueError, match=key):
        HeartbeatCadence({key: value})


@pytest.mark.parametrize('heartbeat,report', [(2, 10), (10, 30)])
def test_configuration_boundaries(heartbeat, report):
    cadence = HeartbeatCadence({'worker_heartbeat_seconds': heartbeat, 'worker_report_seconds': report})
    assert cadence.interval == heartbeat and cadence.report_interval == report


@pytest.mark.parametrize('busy_claim,config_change,reject_heartbeat', [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_worker_main_loop_polls_independently_and_keeps_heartbeats(execution, monkeypatch, busy_claim, config_change, reject_heartbeat):
    import uuid
    from types import SimpleNamespace
    from worker import client, control, accelerators, probe_credentials, heartbeat_cadence, process_logs
    worker, task, _ = execution
    worker.config.update(mock=True, node_name='cadence-test')
    clock = [100.]
    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(heartbeat_cadence.random, 'uniform', lambda a, b: 1.)
    def wait(seconds):
        assert 0 < seconds <= 3
        clock[0] += seconds
        assert clock[0] < 135, 'main loop failed to progress'
        return False
    monkeypatch.setattr(client.time, 'sleep', wait)
    monkeypatch.setattr(worker.stop_event, 'wait', wait)
    def report():
        return {'host': {'revision': 2 if config_change and clock[0] >= 102 else 1},
                'disk': {'free_mb': 100, 'min_free_mb': 0}}
    controller = SimpleNamespace(source=None, refresh=lambda *a: None, adopt=lambda: None,
                                 ready=lambda: True, report=report)
    monkeypatch.setattr(control, 'Control', lambda *a: controller)
    monkeypatch.setattr(accelerators, 'capabilities', lambda c: [])
    monkeypatch.setattr(probe_credentials, 'available', lambda c: [])
    monkeypatch.setattr(client, 'Retention', lambda *a: SimpleNamespace(start=lambda: None,
        remember=lambda root: None, mark_ready=lambda e: None))
    monkeypatch.setattr(process_logs, 'LogService', lambda *a: SimpleNamespace(start=lambda: None, close=lambda: None))
    beats, claims, rejections = [], [], []
    def post(path, body=None):
        if path == 'worker/register':
            return {'slot_id': str(uuid.uuid4()), 'resource_source': 'agent'}
        if path == 'worker/heartbeat':
            if reject_heartbeat and not rejections:
                rejections.append(clock[0])
                raise client.APIError(401, 'rejected')
            beats.append((clock[0], body))
            return {'resource_source': 'agent'}
        if path.endswith('/claim'):
            claims.append(clock[0])
            if busy_claim and len(claims) == 1:
                raise client.APIError(503, 'busy', '8')
            return {'cleanup': [], 'task': task if clock[0] >= 122 else None}
        raise AssertionError(path)
    worker.api.post = post
    def launch(child, task):
        child.state = None
        worker.children[task['execution_id']] = child
    monkeypatch.setattr(worker, 'launch', launch)
    try:
        worker.run(once=True)
    finally:
        worker.slot_lock.close()
    times = [at for at, body in beats]
    assert times[:2] == ([102, 107] if reject_heartbeat else [100, 102] if config_change else [100, 105])
    assert max(b-a for a,b in zip(times, times[1:])) <= 5
    assert all('disk' in body for _,body in beats)
    full = [(at,body) for at,body in beats if 'host' in body]
    assert full[0][0] == (102 if reject_heartbeat else 100)
    assert full[-1][0] == (122 if config_change or reject_heartbeat else 120)
    if busy_claim:
        assert claims[1] >= 108
        assert any(100 < at < claims[1] for at in times)
    else:
        assert claims[1] == (104 if reject_heartbeat else 102)
        assert max(b-a for a,b in zip(claims,claims[1:])) <= 2
    assert controller.source == 'agent'


from backend.tests.test_worker_execution_recovery import execution


def test_release_contains_cadence_module():
    import io
    import zipfile
    from backend.app.worker_upgrades import build_release
    with zipfile.ZipFile(io.BytesIO(build_release().archive)) as archive:
        assert 'worker/heartbeat_cadence.py' in archive.namelist()
        assert b'HeartbeatCadence' in archive.read('worker/client.py')


def test_invalid_config_rejected_before_registration(execution):
    worker, _, calls = execution
    worker.config['worker_heartbeat_seconds'] = 0
    with pytest.raises(ValueError, match='worker_heartbeat_seconds'):
        worker.run()
    assert calls == []


def test_periodic_full_deadline_coalesces_with_next_base_heartbeat():
    clock, cadence, body = setup()
    cadence.accepted(body)
    clock[0] = 119.8
    sent = cadence.prepare(body)
    assert 'host' not in sent
    cadence.accepted(body)
    clock[0] = 120
    assert cadence.prepare(body) is None
    assert cadence.delay() == 2
    clock[0] = 124.8
    assert cadence.prepare(body) == body
