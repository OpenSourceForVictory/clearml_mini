import pytest
import json
import sys

from scripts import validate_multinode_batch as script
from scripts.validate_multinode_batch import max_overlap


def test_overlap_counts_tasks_and_distinct_nodes():
    intervals = [
        ('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:05Z'),
        ('node-a', '2026-01-01T00:00:01Z', '2026-01-01T00:00:04Z'),
        ('node-b', '2026-01-01T00:00:02Z', '2026-01-01T00:00:03Z'),
    ]
    assert max_overlap(intervals) == (3, 2)
    assert max_overlap([]) == (0, 0)
    assert max_overlap([('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')]) == (0, 0)


def test_touching_intervals_do_not_overlap_and_reversed_is_rejected():
    assert max_overlap([
        ('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z'),
        ('node-b', '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z'),
    ]) == (1, 1)
    with pytest.raises(ValueError, match='finished before'):
        max_overlap([('node-a', '2026-01-01T00:00:02Z', '2026-01-01T00:00:01Z')])


class Reply:
    def __init__(self, status, data):
        self.status_code = status
        self.data = data
        self.text = json.dumps(data)
        self.is_success = 200 <= status < 300

    def json(self):
        return self.data


class BatchAPI:
    """In-memory public-API contract for the disposable batch CLI."""
    def __init__(self, *, preflight='ready', failed=False, retry_login=False):
        self.headers = {}
        self.calls = []
        self.preflight = preflight
        self.failed = failed
        self.retry_login = retry_login
        self.closed = False
        self.cases = [
            {'case_id': f'case-{index:03d}', 'status': 'FAILED' if failed and index == 1 else 'SUCCEEDED',
             'error': 'business failed' if failed and index == 1 else None,
             'job_id': f'job-{index}', 'run_id': f'run-{index}',
             'placement': {'node_name': f'node-{index}'},
             'outcomes': {'wrapper_exit_code': 1 if failed and index == 1 else 0}}
            for index in range(2)
        ]

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        if path == 'auth/login':
            if self.retry_login:
                self.retry_login = False
                return Reply(503, {'detail': 'busy'})
            return Reply(200, {'access_token': 'test-only-token'})
        if path == 'admin/pools':
            return Reply(200, [{'id': 'pool', 'name': 'two-node-test', 'enabled': True,
                                'ready_count': 2}])
        if path == 'admin/client-templates' and method == 'POST':
            return Reply(201, {'id': 'template'})
        if path == 'admin/client-templates/template' and method == 'PATCH':
            return Reply(200, {'enabled': False})
        if path == 'batches/preflight':
            return Reply(200, {'status': self.preflight})
        if path == 'batches' and method == 'POST':
            return Reply(201, {'id': 'batch'})
        if path == 'batches/batch':
            return Reply(200, {'status': 'FAILED' if self.failed else 'SUCCEEDED',
                               'succeeded': 1 if self.failed else 2, 'finished': 2,
                               'control_revision': 0, 'cases': self.cases})
        if path == 'batches/batch/control':
            return Reply(200, {'action': 'cancel'})
        if path.startswith('jobs/') and '/logs?' in path:
            return Reply(200, {'chunks': [{'content': 'MULTINODE_SMOKE'}]})
        if path.startswith('jobs/'):
            return Reply(200, {'attempts': [{'cleanup_confirmed': True,
                 'timing': {'running_started_at': '2026-01-01T00:00:00Z',
                            'execution': {'finished_at': '2026-01-01T00:00:10Z'}}}]})
        raise AssertionError((method, path))

    def close(self):
        self.closed = True


def run_cli(monkeypatch, tmp_path, api):
    evidence = tmp_path / 'proof.json'
    monkeypatch.setattr(script.httpx, 'Client', lambda **kwargs: api)
    monkeypatch.setattr(script.getpass, 'getpass', lambda prompt: 'test-only-password')
    monkeypatch.setattr(script.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sys, 'argv', ['validate_multinode_batch.py', '--pool-id', 'pool',
                         '--tasks', '2', '--max-concurrent', '2', '--min-nodes', '2',
                         '--sleep-seconds', '1', '--deadline-seconds', '31',
                         '--evidence', str(evidence)])
    return evidence


def test_cli_submits_checks_placement_logs_and_cleanup(monkeypatch, tmp_path):
    api = BatchAPI(retry_login=True)
    evidence = run_cli(monkeypatch, tmp_path, api)
    script.main()
    result = json.loads(evidence.read_text())
    assert result['succeeded'] == result['cleanup_confirmed'] == 2
    assert result['recorded_task_peak'] == result['recorded_node_peak'] == 2
    assert result['sample_command_marker_by_node'] == {'node-0': True, 'node-1': True}
    assert result['transient_http'] == {'503': 1}
    submitted = next(body for method, path, body in api.calls if method == 'POST' and path == 'batches')
    assert submitted['max_concurrent'] == 2 and len(submitted['cases']) == 2
    assert submitted['client']['template_id'] == 'template'
    assert ('PATCH', 'admin/client-templates/template', {'enabled': False}) in api.calls
    assert api.closed


@pytest.mark.parametrize('failure', ['preflight', 'business'])
def test_cli_rejects_bad_preflight_or_failed_execution_and_cleans_up(monkeypatch, tmp_path,
                                                                      failure):
    api = BatchAPI(preflight='blocked' if failure == 'preflight' else 'ready',
                   failed=failure == 'business')
    evidence = run_cli(monkeypatch, tmp_path, api)
    with pytest.raises((RuntimeError, AssertionError)):
        script.main()
    result = json.loads(evidence.read_text())
    assert result['error']
    assert ('PATCH', 'admin/client-templates/template', {'enabled': False}) in api.calls
    assert any(path == 'batches/batch/control' for _, path, _ in api.calls) == (failure == 'business')
    assert api.closed
