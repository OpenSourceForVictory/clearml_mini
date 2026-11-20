"""Original compose commands retain optional, read-only shared artifact access."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from backend.app import shared_storage

ROOT = Path(__file__).resolve().parents[2]


def render(tmp_path, values, overlay=False):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose is required for deployment rendering')
    probe = subprocess.run(['docker', 'compose', 'version'], capture_output=True)
    if probe.returncode:
        pytest.skip('Docker Compose plugin is unavailable')
    env_file = tmp_path/'compose.env'
    env_file.write_text('')
    # Do not consume developer credentials or deployment .env files.
    env = {'PATH': os.environ['PATH'], 'HOME': str(tmp_path),
           'POSTGRES_PASSWORD': 'test-only', 'ADMIN_USERNAME': 'test-only',
           'ADMIN_PASSWORD': 'test-only', 'JWT_SECRET': 'test-only', **values}
    cmd = ['docker', 'compose', '--env-file', str(env_file), '-p', 'shared-config-test',
           '-f', str(ROOT/'deploy/compose.yml')]
    if overlay:
        cmd += ['-f', str(ROOT/'deploy/shared-storage-compose.yml')]
    result = subprocess.run([*cmd, 'config', '--format', 'json'], env=env,
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('root_value', [None, ''])
def test_unconfigured_original_command(tmp_path, monkeypatch, root_value):
    values = {} if root_value is None else {'SHARED_ARTIFACT_HOST_ROOT': root_value}
    config = render(tmp_path, values)
    api = config['services']['api']
    root = next(v for v in api['volumes'] if v['target'] == '/mnt/mini-eval-artifacts')
    assert Path(root['source']) == ROOT/'deploy/empty-artifacts'
    assert Path(root['source']).is_dir() and root['read_only']
    assert root['bind']['create_host_path'] is False
    monkeypatch.setenv('SHARED_ARTIFACT_HOST_ROOT', api['environment']['SHARED_ARTIFACT_HOST_ROOT'])
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', api['environment']['MINI_EVAL_SHARED_STORAGE'])
    assert shared_storage.mappings() == []
    assert api['build']['dockerfile'] == 'backend/Dockerfile'
    for service in ('db', 'migrate', 'bootstrap', 'onboarding'):
        assert all(v['target'] != root['target'] for v in config['services'][service].get('volumes', []))


@pytest.mark.parametrize('overlay', [False, True])
@pytest.mark.parametrize('advanced', ['', '[]'])
def test_configured_and_legacy_commands(tmp_path, monkeypatch, overlay, advanced):
    host = tmp_path/'artifacts with spaces'
    host.mkdir()
    config = render(tmp_path, {'SHARED_ARTIFACT_HOST_ROOT': str(host),
                              'MINI_EVAL_SHARED_STORAGE': advanced}, overlay)
    api = config['services']['api']
    mounts = [v for v in api['volumes'] if v['target'] == '/mnt/mini-eval-artifacts']
    assert len(mounts) == 1
    assert mounts[0]['source'] == str(host) and mounts[0]['read_only']
    assert mounts[0]['bind']['create_host_path'] is False
    for key in ('SHARED_ARTIFACT_HOST_ROOT', 'MINI_EVAL_SHARED_STORAGE'):
        monkeypatch.setenv(key, api['environment'][key])
    assert bool(shared_storage.mappings()) == (not advanced)


def test_missing_root_not_created_by_render(tmp_path):
    absent = tmp_path/'missing'
    config = render(tmp_path, {'SHARED_ARTIFACT_HOST_ROOT': str(absent)})
    mount = next(v for v in config['services']['api']['volumes'] if v['target'] == '/mnt/mini-eval-artifacts')
    assert mount['source'] == str(absent) and not mount['bind']['create_host_path']
    assert not absent.exists()


def test_custom_advanced_mapping_preserved(tmp_path, monkeypatch):
    mapping = dict(id='existing', node_names=['specific-node'], node_root='/node/artifacts',
                   api_root='/mnt/mini-eval-artifacts', marker='existing-storage-001')
    config = render(tmp_path, {'SHARED_ARTIFACT_HOST_ROOT': str(tmp_path),
                              'MINI_EVAL_SHARED_STORAGE': json.dumps([mapping])})
    env = config['services']['api']['environment']
    monkeypatch.setenv('SHARED_ARTIFACT_HOST_ROOT', env['SHARED_ARTIFACT_HOST_ROOT'])
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', env['MINI_EVAL_SHARED_STORAGE'])
    assert shared_storage.mappings() == [mapping]
