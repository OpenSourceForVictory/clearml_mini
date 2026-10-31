"""Explicitly opt in: disposable privileged Docker mount namespace, read-only repo."""
import os
from pathlib import Path
import subprocess
import pytest

@pytest.mark.skipif(os.getenv('RUN_RETENTION_MOUNT_TEST') != '1', reason='explicit disposable mount-namespace test required')
def test_same_filesystem_directory_and_file_bind_mount_boundaries():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(['docker', 'run', '--rm', '--privileged', '--network', 'none',
        '-e', 'PYTHONPATH=/repo', '-v', str(root)+':/repo:ro', '-w', '/repo', '--entrypoint', 'python',
        os.environ.get('STORAGE_PROBE_TEST_IMAGE', 'mini-eval-client-base:1'),
        'backend/tests/fixtures/retention_mount_boundary.py'], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert '"external_asset": "unchanged"' in result.stdout
