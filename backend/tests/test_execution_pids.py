"""PID policy across host launch snapshots, validation and Docker argv."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_host_backend import worker as host_worker
from backend.tests.test_host_backend_lifecycle import backend
from backend.tests.test_worker_docker_contracts import container
from worker import resource_limits as limits
from worker.host_backend import HostBackend


@pytest.mark.parametrize('value', ['missing', None, 1, 355442, 3355442])
def test_host_snapshot_and_start_share_limit(tmp_path, monkeypatch, value):
    (tmp_path/'app.py').write_text('pass')
    w=host_worker(tmp_path,['python3','app.py'])
    if value != 'missing':w.config['execution_pids_limit']=value
    else:value=None
    host=HostBackend(w);host.system=False
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    host.create(w.state['task'])
    launch=json.loads((host.root/'launch.json').read_text())
    assert launch['resource_limits']['pids']==value
    # A config edit between create/start must not change the launch snapshot.
    w.config['execution_pids_limit']=777
    calls=[];verified=[]
    monkeypatch.setattr('worker.host_backend.subprocess.run',lambda argv,**kw:(calls.append(argv) or SimpleNamespace(returncode=0,stderr='')))
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout='/job'))
    monkeypatch.setattr(limits,'verify_group',lambda *a:verified.append(a))
    host.start(w.state['task'])
    props=[arg for arg in calls[0] if arg.startswith('--property=TasksMax=')]
    assert props==['--property=TasksMax='+('infinity' if value is None else str(value))]
    assert verified==[('/job',1,1024**3,value)]


@pytest.mark.parametrize('value', ['missing', None, 1, 3355442])
def test_docker_optional_limit(container,value):
    w,task,calls,_=container
    if value != 'missing':w.config['execution_pids_limit']=value
    else:value=None
    w.create(task)
    assert calls[0][calls[0].index('--pids-limit')+1]==('-1' if value is None else str(value))


@pytest.mark.parametrize('value', [True, False, 0, -1, '256', 1.5, {}, []])
def test_invalid_limit_rejected_before_docker(container,value):
    w,task,calls,_=container;w.config['execution_pids_limit']=value
    with pytest.raises(ValueError,match='execution_pids_limit'):w.create(task)
    assert not calls


@pytest.mark.parametrize('value', [None, 4096, False])
def test_legacy_host_launch_gets_policy_before_marker(backend,monkeypatch,value):
    host,execution=backend;host.system=False
    host.worker.config['execution_pids_limit']=value
    path=host.root/'launch.json';path.write_text(json.dumps({'started':False,'execution_id':execution}))
    calls=[];verified=[]
    monkeypatch.setattr('worker.host_backend.subprocess.run',lambda argv,**kw:(calls.append(argv) or SimpleNamespace(returncode=0,stderr='')))
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout='/job'))
    monkeypatch.setattr(limits,'verify_group',lambda *a:verified.append(a))
    task={'execution_id':execution,'cpus':0,'ram_gb':0,'timeout_seconds':0}
    if value is False:
        with pytest.raises(ValueError,match='execution_pids_limit'):host.start(task)
        assert not calls and not json.loads(path.read_text())['started']
    else:
        host.start(task)
        assert json.loads(path.read_text())['resource_limits']=={'cpus':0,'memory_bytes':0,'pids':value}
        assert verified==[('/job',0,0,value)]


@pytest.mark.parametrize('v2', [True,False])
@pytest.mark.parametrize('actual,requested,valid', [('max',None,True),(None,None,True),('3355442',3355442,True),('32',32,True),('33',32,False),('max',32,False),('0',32,False),('3355443',3355442,False)])
def test_pid_verification_optional_or_bounded(tmp_path,monkeypatch,v2,actual,requested,valid):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    if v2:(tmp_path/'cgroup.controllers').touch()
    root=tmp_path/('job' if v2 else 'pids/job');root.mkdir(parents=True)
    if actual is not None:(root/'pids.max').write_text(actual)
    if valid:limits.verify_group('/job',0,0,requested)
    else:
        with pytest.raises(ValueError,match='pids_limit_not_enforced'):limits.verify_group('/job',0,0,requested)


def test_supervisor_forwards_snapshot(tmp_path,monkeypatch):
    from worker import host_supervisor
    path=tmp_path/'launch.json';path.write_text(json.dumps({'resource_limits':{'cpus':0,'memory_bytes':0,'pids':4096}}))
    seen=[]
    def verify(**kw):
        seen.append(kw)
        raise ValueError('stop-before-business')
    monkeypatch.setattr(limits,'verify_current',verify)
    monkeypatch.setattr(host_supervisor.sys,'argv',['supervisor',str(path)])
    with pytest.raises(ValueError,match='stop-before-business'):host_supervisor.main()
    assert seen==[{'cpus':0,'memory_bytes':0,'pids':4096}]
