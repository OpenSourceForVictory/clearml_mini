"""Ensure the user download is reproducible and works outside the repository."""
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile
from scripts.build_integration_bundle import build, OUTPUT


def test_download_matches_sources_and_runs_without_repository(tmp_path):
    rebuilt = build(tmp_path/'sdk.zip')
    assert rebuilt.read_bytes() == OUTPUT.read_bytes(), 'Regenerate SDK download after source changes'
    extracted = tmp_path/'isolated'
    with ZipFile(rebuilt) as archive:
        assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
        archive.extractall(extracted)
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, 'self_test.py'], cwd=extracted, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout
