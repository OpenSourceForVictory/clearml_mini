"""Single-variable deployment, registration, isolation and compatibility."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.app import shared_storage, shared_file_io
from backend.tests.test_execution import env  # noqa: F401
from backend.tests.test_cs_protocol import prepare


@pytest.fixture
def simple(monkeypatch):
    monkeypatch.delenv('MINI_EVAL_SHARED_STORAGE', raising=False)
    monkeypatch.setenv('SHARED_ARTIFACT_HOST_ROOT', '/data/artifacts')
    return shared_storage.mappings()[0]


def test_default_mapping_identity_and_revocation(simple, monkeypatch):
    assert simple['api_root'] == '/mnt/mini-eval-artifacts'
    for node in ('gpu', 'npu'):
        mapping, relative = shared_storage.match(node, '/data/artifacts/batch/run/output')
        assert relative == 'batch/run/output'
        snapshot = dict(storage_id=mapping['id'], marker=mapping['marker'], node_name=node)
        assert shared_storage.resolve(snapshot) == mapping
    monkeypatch.setenv('SHARED_ARTIFACT_HOST_ROOT', '/data/other')
    with pytest.raises(HTTPException, match='409'):
        shared_storage.resolve(snapshot)


@pytest.mark.parametrize('root', ['/', 'relative', '/data/../etc', '/data\nbad', '/data//bad'])
def test_invalid_root(simple, monkeypatch, root):
    monkeypatch.setenv('SHARED_ARTIFACT_HOST_ROOT', root)
    with pytest.raises(HTTPException) as error:
        shared_storage.mappings()
    assert error.value.status_code == 503


@pytest.mark.parametrize('path', ['/data/artifacts-other/run', '/else/run', '/data/artifacts', '/data/artifacts/../secret'])
def test_outside_root(simple, path):
    with pytest.raises(HTTPException):
        shared_storage.match('node', path)


def test_explicit_precedence_and_empty(simple, monkeypatch):
    advanced = dict(id='legacy', node_names=['only'], node_root='/old/root',
                    api_root='/old/api', marker='legacy-marker-001')
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([advanced]))
    assert shared_storage.mappings() == [advanced]
    assert not shared_storage.allows_node(advanced, 'other')
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', '[]')
    assert shared_storage.mappings() == []
    for bad in ('invalid', json.dumps([{**advanced, 'automatic': True}])):
        monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', bad)
        with pytest.raises(HTTPException):
            shared_storage.mappings()
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', '')
    assert shared_storage.mappings() == [simple]
    monkeypatch.delenv('SHARED_ARTIFACT_HOST_ROOT')
    assert shared_storage.mappings() == []


def test_markerless_files_and_link_rejection(simple, tmp_path):
    mapping = {**simple, 'api_root': str(tmp_path)}
    output = tmp_path/'run'/'output'
    output.mkdir(parents=True)
    (output/'result.json').write_text('{"ok":true}')
    (output/'secret').symlink_to('/etc/passwd')
    query = dict(operation='list', path='')
    result = shared_file_io.filesystem(mapping, 'run/output', query)
    assert result['status'] == 'ready'
    assert any(i['name'] == 'result.json' for i in result['items'])
    assert shared_file_io.filesystem(mapping, 'run/output/secret', query)['status'] == 'unsafe_path'
    assert shared_file_io.filesystem(mapping, '../etc', query)['status'] == 'unsafe_path'
    assert shared_file_io.filesystem(mapping, 'absent', query)['status'] == 'missing'
    assert shared_file_io.filesystem({**mapping, 'api_root': str(tmp_path/'absent')}, 'run', query)['status'] == 'storage_unavailable'
    assert shared_file_io.filesystem({**mapping, 'automatic': False}, 'run/output', query)['status'] == 'storage_unavailable'


def test_claim_registration_and_auth(env, simple, monkeypatch, tmp_path):
    main, c, auth = env
    _, token, _, job_id, task = prepare(env)
    assert task['artifact_transport'] == 'shared'
    registration = f'/api/v1/worker/executions/{task["execution_id"]}/shared-artifacts'
    relative = 'runs/' + task['execution_id'] + '/output'
    body = {'output_root': '/data/artifacts/' + relative}
    assert c.post(registration, headers=token, json=body).status_code == 200
    assert c.post(registration, headers=token, json=body).status_code == 200
    assert c.post(registration, headers=token, json={'output_root': '/etc'}).status_code == 400
    # Isolate the filesystem mount only; real API authorization and stored mapping remain in use.
    monkeypatch.setattr(shared_storage, 'mappings', lambda: [{**simple, 'api_root': str(tmp_path)}])
    out = tmp_path/relative
    out.mkdir(parents=True)
    (out/'result.json').write_text('{}')
    url = f'/api/v1/jobs/{job_id}/attempts/{task["run_id"]}/shared-artifacts'
    assert c.get(url, headers=auth['alice']).status_code == 200
    assert c.get(url, headers=auth['bob']).status_code == 404
    assert c.get(url, headers=auth['alice'], params={'path': '../other'}).status_code == 400
