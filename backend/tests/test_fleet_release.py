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


def fake_api(enabled=True, held=0):
    node = {'id': 'node', 'node_name': TARGETS['192.168.8.157'], 'enabled': enabled,
            'management_revision': 0, 'held_executions': held}
    calls = []
    def api(path, body=None, method=None):
        if body is None: return [dict(node)]
        calls.append(body)
        assert body['expected_revision'] == node['management_revision']
        node.update(enabled=body['enabled'], management_revision=node['management_revision']+1)
        return dict(node)
    plan = {'targets': [{'address': '192.168.8.157', 'node_name': node['node_name']}]}
    return api, node, calls, plan


def test_drain_restore_owned_changes(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        assert not node['enabled']
        restore_admission(api, plan, j)
        restore_admission(api, plan, j)
        assert node['enabled'] and len(calls) == 2


def test_preserve_preexisting_disabled_node(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api(enabled=False)
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        restore_admission(api, plan, j)
    assert not node['enabled'] and not calls


def test_drain_timeout_retains_task_and_can_restore(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api(held=1)
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(TimeoutError): drain(api, plan, j, timeout=0)
        assert node['held_executions'] == 1
        restore_admission(api, plan, j)
        assert node['enabled']


def test_restore_does_not_overwrite_admin_change(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        node['management_revision'] += 1
        with pytest.raises(RuntimeError): restore_admission(api, plan, j)
    assert not node['enabled'] and len(calls) == 1


def test_lost_patch_response_not_retried(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    def uncertain(path, body=None, method=None):
        response = api(path, body, method)
        if body is not None: raise RuntimeError('connection lost after mutation')
        return response
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(RuntimeError): drain(uncertain, plan, j)
        with pytest.raises(RuntimeError): drain(uncertain, plan, j)
        with pytest.raises(RuntimeError): restore_admission(api, plan, j)
    assert len(calls) == 1


from backend.tests.test_execution import env


def test_real_api_drain_keeps_lease_and_blocks_new_claim(env, tmp_path):
    from backend.tests.test_gpu_sharing import setup_gpu, submit_gpu
    from backend.app.scheduling import tick
    from backend.app.core import Worker, Slot
    from scripts.fleet_release_api import drain, restore_admission
    main, client, auth = env
    pool, slots, tokens = setup_gpu(env, 1)
    submit_gpu(env, pool)
    tick(main.SessionLocal)
    task = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0], json={'known_execution_ids': []}).json()['task']
    with main.SessionLocal() as session:
        w = session.get(Worker, session.get(Slot, slots[0]).worker_id)
        w.node_name = TARGETS['192.168.8.157']; session.commit()
    def api(path, body=None, method=None):
        response = client.request(method or 'GET', '/api/v1/' + path, headers=auth['admin'], json=body)
        assert response.status_code == 200, response.text
        return response.json()
    plan = {'targets': [{'address': '192.168.8.157', 'node_name': TARGETS['192.168.8.157']}]}
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(TimeoutError): drain(api, plan, j, timeout=0)
        heartbeat = client.post('/api/v1/worker/heartbeat', headers=tokens[0], json={'slot_id': slots[0], 'execution_id': task['execution_id'], 'started': True})
        assert heartbeat.status_code == 200 and heartbeat.json()['cancel'] is False
        claim = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0], json={'known_execution_ids': [task['execution_id']]})
        assert claim.status_code == 200 and claim.json()['task'] is None
        restore_admission(api, plan, j)


def test_compatibility_gate_rejects_old_worker():
    from scripts.fleet_release_api import assert_release_ready
    api, node, _, plan = fake_api()
    plan.update(target_protocol=10, target_worker='0.12.0', target_agent='1.1.0')
    def incompatible(path, *args):
        if path.endswith('/upgrades'):
            return {'slots': [{'online': True, 'protocol_version': 9, 'software': {'version': '0.10.0'}}]}
        return api(path, *args)
    with pytest.raises(RuntimeError, match='worker release is not ready'):
        assert_release_ready(incompatible, plan, 'digest')


def test_compose_keeps_lan_overlay(tmp_path):
    from scripts.fleet_release_control import compose_command
    command = compose_command(tmp_path/'env', tmp_path/'image.yml')
    files = [command[i+1] for i, value in enumerate(command) if value == '-f']
    assert len(files) == 3
    assert files[0].endswith('/deploy/compose.yml')
    assert files[1].endswith('/deploy/lan-compose.yml')


def test_invalid_backup_never_starts_recovery(tmp_path):
    from scripts.fleet_release_control import restore_probe
    backup = tmp_path/'db.dump'; backup.write_bytes(b'bad backup')
    def never(*a, **kw): pytest.fail('recovery started for invalid backup')
    with pytest.raises(ValueError): restore_probe(backup, 'incorrect', 'image', run=never)
