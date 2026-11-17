"""Systemd identity and artifact boundaries, isolated from host services."""
import json
import uuid
from types import SimpleNamespace

import pytest
from worker.host_backend import HostBackend
from worker.workspace import execution_dirs


@pytest.fixture
def backend(tmp_path):
    execution=str(uuid.uuid4())
    worker=SimpleNamespace(config={},state={'task':{'execution_id':execution}},path=tmp_path/'state',workspace=tmp_path/'workspace')
    (worker.workspace/'runs').mkdir(parents=True)
    backend=HostBackend(worker);backend.root.mkdir(parents=True,exist_ok=True)
    return backend,execution


@pytest.mark.parametrize('mode',['absent','created','started','finished','invalid-exit','wrong-marker','running','exited','wrong-unit','daemon-failure','exit-record'])
def test_inspect_identity_and_recovery(backend,monkeypatch,mode):
    host,execution=backend
    missing=mode in ('absent','created','started','finished','invalid-exit','wrong-marker')
    if mode in ('created','started','finished','invalid-exit','wrong-marker'):
        (host.root/'launch.json').write_text(json.dumps({'execution_id':'wrong' if mode=='wrong-marker' else execution,'started':mode=='started'}))
    if mode in ('finished','invalid-exit'):
        (host.root/'launch.json').write_text(json.dumps({'execution_id':execution,'started':True}))
        (host.root/'exit.json').write_text(json.dumps({'code':7 if mode=='finished' else 'bad'}))
    if mode=='exit-record':(host.root/'exit.json').write_text('{"code":7}')
    response='LoadState=not-found\n' if missing else '\n'.join([
        'LoadState=loaded','Description=Mini Eval execution '+('wrong' if mode=='wrong-unit' else execution),
        'ActiveState=active','SubState='+('running' if mode=='running' else 'dead'),'ExecMainStatus=0'])
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout=response,stderr='offline',returncode=int(mode=='daemon-failure')))
    if mode in ('wrong-marker','wrong-unit','daemon-failure','invalid-exit'):
        with pytest.raises(RuntimeError):host.inspect(execution)
    elif mode in ('absent','started'):assert host.inspect(execution) is None
    else:
        state=host.inspect(execution)['State']
        assert state['Status']==('created' if mode=='created' else 'running' if mode=='running' else 'exited')
        assert state['ExitCode']==(7 if mode in ('exit-record','finished') else 0)
        assert state.get('Recovered',False)==(mode=='finished')


@pytest.mark.parametrize('stuck',[True,False])
def test_stop_requires_confirmed_exit(backend,monkeypatch,stuck):
    host,execution=backend;calls=[];states=iter([True,stuck])
    monkeypatch.setattr(host,'inspect',lambda e:{'State':{'Status':'running','Running':next(states)}})
    monkeypatch.setattr(host,'control',lambda *a,**kw:calls.append(a))
    if stuck:
        with pytest.raises(RuntimeError,match='has not stopped'):host.stop(execution)
    else:host.stop(execution)
    assert calls==[('stop',host.unit(execution))]


def test_stop_of_finished_unloaded_unit_does_not_restart_or_stop(backend,monkeypatch):
    host,execution=backend;calls=[]
    (host.root/'launch.json').write_text(json.dumps({'execution_id':execution,'started':True}))
    (host.root/'exit.json').write_text('{"code":0}')
    monkeypatch.setattr(host,'control',lambda *a,**kw:(calls.append(a) or SimpleNamespace(stdout='LoadState=not-found\n',returncode=0)))
    host.stop(execution)
    assert calls==[('show',host.unit(execution),'--property=LoadState,Description,ActiveState,SubState,ExecMainStatus')]


@pytest.mark.parametrize('cpus,ram_gb',[ (0,0), (0,3), (2,0) ])
def test_host_unlimited_axes_omit_selected_systemd_quotas(backend,monkeypatch,cpus,ram_gb):
    from worker import resource_limits
    host,execution=backend
    (host.root/'launch.json').write_text(json.dumps({'started':False,'execution_id':execution}))
    commands=[];verified=[]
    monkeypatch.setattr('worker.host_backend.subprocess.run',lambda argv,**kwargs:(commands.append(argv) or SimpleNamespace(returncode=0,stderr='')))
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout='/test-group'))
    monkeypatch.setattr(resource_limits,'verify_group',lambda *a:verified.append(a))
    host.start({'execution_id':execution,'cpus':cpus,'ram_gb':ram_gb,'timeout_seconds':60})
    assert any('CPUQuota=' in value for value in commands[0])==bool(cpus)
    assert any('MemoryMax=' in value for value in commands[0])==bool(ram_gb)
    assert verified==[('/test-group',cpus,ram_gb*1024**3)]


def test_remove_preserves_logs_and_bounded_read(backend,monkeypatch):
    host,execution=backend;calls=[]
    monkeypatch.setattr(host,'stop',lambda e:calls.append(e))
    monkeypatch.setattr(host,'control',lambda *a,**kw:calls.append(a))
    (host.root/'launch.json').write_text('{}');(host.root/'stdout.log').write_bytes(b'x'*300000)
    (host.root/'final.json').write_text('{"final":{}}');(host.root/'stderr.log').write_bytes(b'\xff')
    (host.root/'supervisor.log').write_text('supervisor')
    host.remove(execution)
    assert not (host.root/'launch.json').exists()
    out,err=host.logs()
    assert len(out)==262144+len('\n{"final":{}}\n') and out.endswith('{"final":{}}\n')
    assert err=='\ufffdsupervisor'


@pytest.mark.parametrize('mode',['valid','symlink','bad-name','count','size','total'])
def test_artifact_collection_boundaries(backend,monkeypatch,mode):
    import worker.host_backend as module
    host,execution=backend;output=execution_dirs(host.worker.workspace,execution)/'output'
    if mode=='symlink':(output/'escape').symlink_to('/etc/passwd')
    elif mode=='bad-name':(output/'bad name').write_text('no')
    elif mode=='count':
        for i in range(9):(output/f'{i}.txt').write_text('x')
    elif mode in ('size','total'):
        monkeypatch.setattr(module,'MAX_ARTIFACT_BYTES',4 if mode=='size' else 100)
        monkeypatch.setattr(module,'MAX_ARTIFACT_TOTAL',4)
        (output/'one.txt').write_bytes(b'12345')
    else:
        (output/'nested').mkdir();(output/'nested/report.json').write_text('{}')
    if mode=='valid':assert host.artifacts(execution)==[('nested/report.json',b'{}')]
    else:
        with pytest.raises(RuntimeError,match='artifact'):host.artifacts(execution)


@pytest.mark.parametrize('mode',['user','system','npu','launch-fails','limits-fail','duplicate'])
def test_start_enforces_identity_limits_and_no_duplicate(backend,monkeypatch,mode):
    from worker import host_identity,npu_host,resource_limits
    host,execution=backend;host.system=mode in ('system','npu')
    if mode=='npu':host.worker.config['accelerator_type']='npu'
    launch={'started':mode=='duplicate','execution_id':execution}
    (host.root/'launch.json').write_text(json.dumps(launch))
    calls=[];stopped=[];verified=[]
    monkeypatch.setattr('worker.host_backend.os.chown',lambda *a:None)
    monkeypatch.setattr(host_identity,'settings',lambda cfg:SimpleNamespace(pw_name='business',pw_uid=1234,pw_gid=1234))
    monkeypatch.setattr(npu_host,'properties',lambda cfg:['DevicePolicy=closed','DeviceAllow=/dev/davinci0 rw'])
    def run(argv,**kwargs):
        calls.append(argv);return SimpleNamespace(returncode=int(mode=='launch-fails'),stderr='cannot start')
    monkeypatch.setattr('worker.host_backend.subprocess.run',run)
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout='/test-group'))
    monkeypatch.setattr(host,'stop',lambda e:stopped.append(e))
    def verify(*args):
        verified.append(args)
        if mode=='limits-fail':raise RuntimeError('limits not enforced')
    monkeypatch.setattr(resource_limits,'verify_group',verify)
    task={'execution_id':execution,'cpus':2,'ram_gb':3,'timeout_seconds':60}
    if mode in ('duplicate','limits-fail','launch-fails'):
        with pytest.raises(RuntimeError):host.start(task)
    else:host.start(task)
    if mode=='duplicate':assert not calls;return
    assert json.loads((host.root/'launch.json').read_text())['started']
    argv=calls[0]
    for value in ('CPUQuota=200%','MemoryMax=3G','KillMode=control-group','RuntimeMaxSec=60'):
        assert '--property='+value in argv
    assert ('--user' in argv)==(not host.system)
    if mode=='system':assert '--property=User=business' in argv
    if mode=='npu':assert '--property=DeviceAllow=/dev/davinci0 rw' in argv
    assert stopped==([execution] if mode=='limits-fail' else [])
    if mode!='launch-fails':assert verified==[('/test-group',2,3*1024**3)]


def test_root_host_start_uses_bounded_unit_properties(backend,monkeypatch):
    from worker import host_identity,resource_limits
    host,execution=backend;host.system=True
    host.worker.config['host_execution']={'approved':True,'user':'root','uid':0,'gid':0,'allow_root_business':True}
    (host.root/'launch.json').write_text(json.dumps({'started':False,'execution_id':execution}))
    monkeypatch.setattr(host_identity.os,'geteuid',lambda:0)
    monkeypatch.setattr(host_identity.pwd,'getpwnam',lambda name:SimpleNamespace(pw_name='root',pw_uid=0,pw_gid=0,pw_dir='/root'))
    monkeypatch.setattr('worker.host_backend.os.chown',lambda *a:None)
    commands=[]
    monkeypatch.setattr('worker.host_backend.subprocess.run',lambda argv,**kw:(commands.append(argv) or SimpleNamespace(returncode=0,stderr='')))
    monkeypatch.setattr(host,'control',lambda *a,**kw:SimpleNamespace(stdout='/test-group'))
    monkeypatch.setattr(resource_limits,'verify_group',lambda *a:None)
    host.start({'execution_id':execution,'cpus':1,'ram_gb':1,'timeout_seconds':60})
    assert '--property=User=root' in commands[0]
    assert '--property=CapabilityBoundingSet=CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH' in commands[0]
    assert '--property=NoNewPrivileges=yes' in commands[0]
