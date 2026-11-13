import json,os,time,uuid,subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from worker.host_backend import HostBackend
from worker.host_environments import user_environment


def worker(tmp_path,command,timeout=20):
    work=tmp_path/'work';assets=tmp_path/'assets';cache=tmp_path/'cache'
    for p in (work,assets,cache):p.mkdir(exist_ok=True)
    from worker.workspace import validate_storage
    config={'workspace_root':str(work),'assets_root':str(assets),'cache_root':str(cache),'min_free_disk_gb':0}
    validate_storage(config,tmp_path/'state',create=True)
    profile={'python':'/usr/bin/python3','code_root':str(tmp_path)}
    task={'execution_id':str(uuid.uuid4()),'job_id':'example','episodes':2,'timeout_seconds':timeout,'storage_revision':0,'host_revision':1,'host_profile':profile,'cpus':1,'ram_gb':1,'gpu_uuid':None,
          'client_spec':{'contract_version':1,'task_name':'generic','seed':0,'endpoint':'http://192.168.8.151:19092/infer','request_timeout_seconds':2,'template':{'execution_mode':'host','integration_mode':'sdk-command','host_runtime':{'environment_name':'test','environment':{},'overridable_env':[],'assets':[]},'command':command}}}
    return SimpleNamespace(path=tmp_path/'state',state={'task':task,'host_approval':{'revision':1,'environments':{'test':profile}}},config=config,workspace=work,storage_snapshot={'revision':0},runtime_snapshot={'config':{'assets':{}}})


def test_unapproved_environment_never_launches(tmp_path):
    w=worker(tmp_path,['python3','business.py']);w.state['host_approval']['revision']=0
    with pytest.raises(RuntimeError,match='approval'):HostBackend(w).create(w.state['task'])
    assert not Path(str(w.path)+'.host').exists()


def test_unit_identity_not_pid(tmp_path,monkeypatch):
    w=worker(tmp_path,['python3','business.py']);b=HostBackend(w)
    monkeypatch.setattr(b,'control',lambda *a,**k:SimpleNamespace(returncode=0,stdout='LoadState=loaded\nDescription=unrelated\nActiveState=active\nSubState=running',stderr=''))
    with pytest.raises(RuntimeError,match='identity'):b.stop(w.state['task']['execution_id'])


@pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='set HOST_EXECUTION_TEST=1 for real systemd user units')
def test_real_host_stop_and_recovery(tmp_path):
    # Launching an argv process and all of its descendants is controlled by one unit.
    (tmp_path/'business.py').write_text('import time,subprocess,sys\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True)\nPath('+repr(str(tmp_path/'child.pid'))+').write_text(str(p.pid))\ntime.sleep(60)\n')
    w=worker(tmp_path,['python3','business.py']);b=HostBackend(w);task=w.state['task']
    b.create(task)
    launch=json.loads((b.root/'launch.json').read_text());launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')]
    (b.root/'launch.json').write_text(json.dumps(launch))
    try:
        b.start(task)
        end=time.monotonic()+10
        while time.monotonic()<end:
            if (v:=b.inspect(task['execution_id'])) and v['State']['Running']:break
            time.sleep(.1)
        assert v['State']['Running']
        end=time.monotonic()+5
        while not (tmp_path/'child.pid').exists() and time.monotonic()<end:time.sleep(.05)
        pid=int((tmp_path/'child.pid').read_text())
        recovered=HostBackend(w)
        assert recovered.inspect(task['execution_id'])['State']['Running']
        with pytest.raises(RuntimeError,match='duplicate'):recovered.start(task)
        recovered.stop(task['execution_id'])
        assert not ((v:=recovered.inspect(task['execution_id'])) and v['State']['Running'])
        stat=Path('/proc')/str(pid)/'stat'
        assert not stat.exists() or stat.read_text().split()[2]=='Z'
    finally:b.remove(task['execution_id'])


@pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='real systemd opt-in')
def test_real_host_timeout_and_limits(tmp_path):
    (tmp_path/'business.py').write_text('import time;time.sleep(60)')
    w=worker(tmp_path,['python3','business.py'],timeout=1);b=HostBackend(w);task=w.state['task'];b.create(task)
    path=b.root/'launch.json';launch=json.loads(path.read_text());launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(launch))
    try:
        b.start(task)
        limits=b.control('show',b.unit(task['execution_id']),'--property=MemoryMax,CPUQuotaPerSecUSec').stdout
        assert 'MemoryMax=1073741824' in limits and 'CPUQuotaPerSecUSec=1s' in limits
        end=time.monotonic()+8
        while time.monotonic()<end:
            v=b.inspect(task['execution_id'])
            if v and not v['State']['Running']:break
            time.sleep(.1)
        assert v and not v['State']['Running'] and v['State']['ExitCode']!=0
    finally:b.remove(task['execution_id'])

@pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='real systemd opt-in')
def test_worker_watchdog_stops_host_when_lease_lost(tmp_path,monkeypatch):
    from worker.client import Worker
    import urllib.error
    fake=worker(tmp_path,['python3','business.py'])
    task={**fake.state['task'],'benchmark':'cs-client','status':'STARTING','lease_seconds':3}
    (tmp_path/'business.py').write_text('import time;time.sleep(60)')
    cfg={**fake.config,'_host_approval':fake.state['host_approval']}
    w=Worker(cfg,tmp_path/'lease-state',snapshot={'revision':0,'config':fake.config},runtime_snapshot={'revision':0,'config':{'images':[],'assets':{}}});w.slot_id='test'
    completed=[]
    class API:
        calls=0
        def post(self,path,data=None):
            if path=='worker/heartbeat':
                self.calls+=1
                if self.calls==1:return {'lease_seconds':3,'cancel':False}
                raise urllib.error.URLError('simulated disconnect')
            completed.append(path);return {}
    w.api=API()
    original=HostBackend.create
    def create(backend,task):
        original(backend,task);path=backend.root/'launch.json';v=json.loads(path.read_text());v['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(v))
    monkeypatch.setattr(HostBackend,'create',create)
    w.execute(task)
    assert any(x.endswith('/complete') for x in completed)
    assert w.state is None
