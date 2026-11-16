"""Real subprocess coverage for independent host interpreter and business cwd."""
import json
import os
from pathlib import Path
import subprocess
import venv
from types import SimpleNamespace as N

import pytest
from backend.tests.test_host_backend import worker
from runner.template_contract import host_config
from worker.host_backend import HostBackend


@pytest.mark.parametrize('value',['', '../escape', '/safe/../escape', 'a\x00b', '${work_dir}', 1, 'a\nb'])
def test_contract_rejects_invalid_directory(value):
    with pytest.raises(ValueError,match='working_directory'):
        host_config({'environment_name':'test','working_directory':value})


def prepare(tmp_path, monkeypatch, mode, directory='business'):
    # Keep environment and business code in different directories, as in Conda/venv.
    venv.EnvBuilder(with_pip=False).create(tmp_path/'python-env')
    python=str(tmp_path/'python-env/bin/python')
    business=tmp_path/'business';business.mkdir()
    (business/'settings.txt').write_text('relative-config')
    script='''import json,os,sys
from pathlib import Path
assert Path('settings.txt').read_text() == 'relative-config'
record={'python':sys.executable,'cwd':str(Path.cwd()),'work':os.environ['MINI_EVAL_WORK_DIR']}
Path(os.environ['MINI_EVAL_OUTPUT_DIR'],'observed.json').write_text(json.dumps(record))
if 'MINI_EVAL_RESULT' in os.environ:
    Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True}))
else:
    print(json.dumps({'final':{'success_rate':1.0,'episode_count':2,'runtime':0.01}}),flush=True)
'''
    (business/'app.py').write_text(script)
    w=worker(tmp_path,['python3','app.py'])
    task=w.state['task'];profile={'python':python,'code_root':str(tmp_path)}
    task['host_profile']=profile;w.state['host_approval']['environments']['test']=profile
    spec=task['client_spec'];spec['service_mode']='none'
    template=spec['template'];template['integration_mode']=mode if mode!='service' else 'direct'
    template['host_runtime']['working_directory']=directory
    if mode=='command':template.update(task_type='command',integration_mode='direct')
    if mode=='service':spec.update(workload='service',service={'port':19000,'address':'127.0.0.1'})
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    backend=HostBackend(w);backend.create(task)
    launch=json.loads((backend.root/'launch.json').read_text())
    return backend,launch,business,python


@pytest.mark.parametrize('mode',['direct','sdk-command','command','service'])
def test_real_entrypoints_use_selected_interpreter_and_cwd(tmp_path,monkeypatch,mode):
    backend,launch,business,python=prepare(tmp_path,monkeypatch,mode)
    result=subprocess.run(launch['command'],env=launch['environment'],cwd=launch['cwd'],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    observed=json.loads((Path(launch['environment']['MINI_EVAL_OUTPUT_DIR'])/'observed.json').read_text())
    assert observed['python']==python
    assert observed['cwd']==str(business)
    assert observed['work']!=observed['cwd']
    if mode=='sdk-command':
        assert result.stdout.count('"status":"COMPLETED"')==2


@pytest.mark.parametrize('directory',['missing','../outside'])
def test_bad_directory_fails_before_launch(tmp_path,monkeypatch,directory):
    with pytest.raises((RuntimeError,ValueError),match='working_directory'):
        prepare(tmp_path,monkeypatch,'direct',directory)


def test_symlink_escape_rejected(tmp_path,monkeypatch):
    (tmp_path/'link').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(RuntimeError,match='working_directory'):
        prepare(tmp_path,monkeypatch,'direct','link')


def test_absolute_directory_and_legacy_default(tmp_path,monkeypatch):
    backend,launch,business,_=prepare(tmp_path,monkeypatch,'direct',str(tmp_path/'business'))
    assert json.loads(Path(launch['environment']['MINI_EVAL_INPUT']).read_text())['_resolved_cwd']==str(business)
    task=backend.worker.state['task'];del task['client_spec']['template']['host_runtime']['working_directory']
    task['client_spec']['template']['command']=['python3','business/app.py']
    backend.create(task)
    config=json.loads(Path(launch['environment']['MINI_EVAL_INPUT']).read_text())
    assert config['_resolved_cwd']==launch['environment']['MINI_EVAL_WORK_DIR']


def test_old_worker_gets_actionable_blocker():
    from backend.app.host_environments import blockers
    from backend.app.core import now
    spec={'template':{'execution_mode':'host','host_runtime':{'working_directory':'.'}}}
    assert '13' in blockers(None,N(protocol_version=12),spec,now())[0]
    from backend.app.scheduling import eligibility_codes
    slot=N(protocol_version=12,disk_min_free_mb=None)
    assert eligibility_codes(slot,None,N(client_spec=spec),[],now())==['client_runtime']

@pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='requires systemd user manager')
@pytest.mark.parametrize('mode',['direct','sdk-command','command','service'])
def test_real_systemd_business_directory(tmp_path,monkeypatch,mode):
    import time
    backend,launch,business,python=prepare(tmp_path,monkeypatch,mode)
    task=backend.worker.state['task']
    try:
        backend.start(task)
        result=Path(launch['environment']['MINI_EVAL_OUTPUT_DIR'])/'observed.json'
        deadline=time.monotonic()+15
        while not result.exists() and time.monotonic()<deadline:time.sleep(.1)
        assert result.is_file(),backend.logs()
        record=json.loads(result.read_text())
        assert record['cwd']==str(business) and record['python']==python
        assert record['work']!=str(business)
    finally:
        backend.remove(task['execution_id'])
