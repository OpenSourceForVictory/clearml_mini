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
    guide = Path('examples/integration/README.md').read_bytes()
    assert Path('frontend/public/integration/README.md').read_bytes() == guide
    extracted = tmp_path/'isolated'
    with ZipFile(rebuilt) as archive:
        assert 'runner/process_command.py' in archive.namelist()
        assert archive.read('README.md') == guide
        assert all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist())
        archive.extractall(extracted)
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, 'self_test.py'], cwd=extracted, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS:' in result.stdout


def test_download_contains_negotiated_process_log_runtime(tmp_path):
    extracted=tmp_path/'isolated'
    with ZipFile(build(tmp_path/'sdk.zip')) as archive:
        archive.extractall(extracted)
    code='''import json,sys,uuid
from pathlib import Path
from runner.process_log_store import initialize,locked,read_json
from runner.process_log_run import run
root=Path('logs').resolve();execution=str(uuid.uuid4());generation=str(uuid.uuid4())
initialize(root,execution,generation)
config={'_process_logs':{'root':str(root),'execution_id':execution,'generation':generation}}
assert run([sys.executable,'-c',"print('isolated SDK log')"],config=config,scope='business')==0
with locked(root,False) as fd: index=read_json(fd,'manifest.json')
entry=next(iter(index['scopes'].values()))
assert entry['status']=='closed' and entry['next_sequence']>0
assert any('isolated SDK log' in p.read_text() for p in root.rglob('*.jsonl'))
'''
    env=dict(os.environ);env.pop('PYTHONPATH',None)
    result=subprocess.run([sys.executable,'-c',code],cwd=extracted,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
