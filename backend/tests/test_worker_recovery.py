import uuid
from worker.client import APIError, Worker


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
