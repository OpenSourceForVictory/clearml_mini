import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet_release import Checkpoints, TARGETS, atomic_json, probe, release_plan, verify_identity


def facts(host):
    return {'addresses': host, 'hostname': 'node', 'machine_id': host,
            'worker': {'SOFTWARE_VERSION': '0.10.0', 'PROTOCOL_VERSION': 9},
            'agent': {'VERSION': '1.0.0'}, 'mount': 'nfs4', 'gpus': 'GPU-' + host,
            'services': {x: 'ActiveState=active\nSubState=running' for x in
                         ('mini-eval-worker-nfs', 'mini-eval-agent', 'mini-eval-updater')}}


def observed(host):
    return probe(host, run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(facts(host))))


def test_no_outside_target_execution():
    def never(*a, **kw): pytest.fail('out of scope command executed')
    with pytest.raises(ValueError): probe('192.168.8.174', run=never)


def test_strict_ssh_and_allowlisted_output():
    def run(argv, **kwargs):
        assert 'StrictHostKeyChecking=yes' in argv
        assert 'BatchMode=yes' in argv
        assert kwargs['timeout'] == 55
        return SimpleNamespace(returncode=0, stdout=json.dumps({**facts('192.168.8.157'), 'token': 'secret'}))
    result = probe('192.168.8.157', run)
    assert result['status'] == 'observed'
    assert 'secret' not in json.dumps(result)


def test_wrong_host_rejected():
    result = probe('192.168.8.157', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(facts('192.168.8.174'))))
    assert result['status'] == 'unknown'
    assert result['error'] == 'address_identity_mismatch'


def test_failures_not_dumped():
    result = probe('192.168.8.157', lambda *a, **kw: SimpleNamespace(returncode=255, stdout='secret', stderr='secret'))
    assert result['status'] == 'unknown'
    assert 'secret' not in json.dumps(result)


def test_partial_plan_rejected():
    with pytest.raises(ValueError): release_plan({'nodes': [observed('192.168.8.157')]})


def test_plan_and_changed_identity():
    plan = release_plan({'observed_at': 'now', 'nodes': [observed(h) for h in TARGETS]})
    assert plan['target_protocol'] == 10
    expected = plan['targets'][0]
    verify_identity(expected, observed(expected['address']))
    with pytest.raises(ValueError): verify_identity(expected, {**observed(expected['address']), 'machine_id': 'other'})


def test_private_atomic_file(tmp_path):
    p = tmp_path / 'state.json'
    atomic_json(p, {'ok': True})
    assert p.stat().st_mode & 0o777 == 0o600
    atomic_json(p, {'ok': False})
    assert json.loads(p.read_text()) == {'ok': False}


def test_checkpoints_idempotent_and_input_bound(tmp_path):
    with Checkpoints(tmp_path, 'one').locked() as journal:
        assert journal.begin('inventory', {'nodes': list(TARGETS)})
        journal.finish('inventory')
    with Checkpoints(tmp_path, 'one').locked() as journal:
        assert not journal.begin('inventory', {'nodes': list(TARGETS)})
        with pytest.raises(ValueError): journal.begin('inventory', {'nodes': []})


def test_uncertain_step_is_not_repeated(tmp_path):
    with Checkpoints(tmp_path, 'one').locked() as journal:
        journal.begin('upgrade', {'target': '192.168.8.157'})
    with Checkpoints(tmp_path, 'one').locked() as journal:
        with pytest.raises(RuntimeError): journal.begin('upgrade', {'target': '192.168.8.157'})


def test_different_releases_share_lock(tmp_path):
    with Checkpoints(tmp_path, 'one').locked():
        with pytest.raises(BlockingIOError):
            with Checkpoints(tmp_path, 'two').locked(): pytest.fail('lock bypassed')


def test_no_path_traversal(tmp_path):
    with pytest.raises(ValueError): Checkpoints(tmp_path, '../other')
