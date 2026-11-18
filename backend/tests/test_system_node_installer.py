"""Installer transaction tests: real files with OS/account boundaries substituted."""
import json
import os
import subprocess
REAL_RUN=subprocess.run
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from scripts import install_system_node as m


def missing(*a):raise KeyError()

@pytest.fixture
def setup(tmp_path,monkeypatch):
    control=tmp_path/'control';units=tmp_path/'units';units.mkdir()
    source=tmp_path/'source';(source/'scripts').mkdir(parents=True)
    for name in ('worker','runner','node_agent'):
        (source/name).mkdir();(source/name/'__init__.py').write_text('')
    (source/'release.sha256').write_text('a'*64)
    monkeypatch.setattr(m,'CONTROL',control);monkeypatch.setattr(m,'WORKSPACES',tmp_path/'workspaces')
    monkeypatch.setattr(m,'UPDATER_ROOT',tmp_path/'updater');monkeypatch.setattr(m,'UNIT_DIR',units);monkeypatch.setattr(m,'INSTALL_LOCK',tmp_path/'install.lock')
    monkeypatch.setattr(m,'__file__',str(source/'scripts/install_system_node.py'))
    monkeypatch.setattr(os,'geteuid',lambda:0)
    acc=N(pw_name='root',pw_uid=0,pw_gid=0,pw_dir='/root')
    monkeypatch.setattr(m.pwd,'getpwnam',lambda name:acc if name=='root' else missing())
    monkeypatch.setattr(os,'chown',lambda *args:None)
    calls=[]
    monkeypatch.setattr(m.subprocess,'run',lambda args,**kw:(calls.append(args) or N(returncode=0,stdout='',stderr='')))
    monkeypatch.setattr('scripts.install_worker.identity',lambda *a:None)
    monkeypatch.setattr('scripts.install_worker.discover',lambda *a:[0,1])
    monkeypatch.setattr('worker.host_identity.verify_system',lambda *a:None)
    monkeypatch.setattr('worker.host_identity.check_paths',lambda *a:None)
    monkeypatch.setattr('worker.npu_host.probe',lambda *a:None)
    monkeypatch.setattr(m,'discover_npu',lambda:[0,1])
    def updater(layout,system):
        assert system
        for name in ('mini-eval-agent.service','mini-eval-updater.service'):(units/name).write_text('test')
    monkeypatch.setattr('scripts.install_node.install_updater',updater)
    monkeypatch.setattr('worker.updater.Updater',lambda layout:N(control=lambda action:calls.append(['worker',action])))
    credential={'node_name':'test','api_url':'http://test','token':'TEST-ONLY','execution_modes':['host'],'runner_image':'test'}
    return credential,calls,control,units

@pytest.mark.parametrize('kind',['gpu','npu'])
def test_multicard_private_config_and_system_services(setup,kind):
    credential,calls,control,units=setup
    result=m.install({**credential,'accelerator_type':kind},'all')
    assert len(result['config_paths'])==2
    values=[json.loads(Path(p).read_text()) for p in result['config_paths']]
    assert {v['gpu_index'] for v in values}=={0,1}
    assert len({v['state_path'] for v in values})==2
    for value,path,service in zip(values,result['config_paths'],result['services']):
        assert value['host_execution']['user']=='root'
        assert value['host_execution']['allow_root_business'] is True
        assert value['workspace_root']==str(m.WORKSPACES/'test'/f'{kind}{value["gpu_index"]}')
        assert value['assets_root']==str(m.WORKSPACES/'test'/'assets')
        assert value['cache_root']==str(m.WORKSPACES/'test'/'cache')
        assert Path(path).stat().st_mode&0o777==0o600
        assert (control/'.config').stat().st_mode&0o777==0o700
        assert service['scope']=='system'
        REAL_RUN(['systemd-analyze','verify','--man=no',str(units/service['name'])],check=True,capture_output=True)
        assert 'User=root\n' in (units/service['name']).read_text()
        assert '\nWorkingDirectory='+result['app_dir']+'\n' in (units/service['name']).read_text()
        assert Path(value['cache_root']).stat().st_mode&0o777==0o755
        if kind=='npu':
            assert value['npu_runtime']['host']['user']=='root'
            assert value['npu_runtime']['allow_shared'] is False
        else:
            assert value['gpu_runtime']['allow_shared'] is False
    assert calls[-2:]==[['worker','enable'],['worker','start']]
    before={p:p.read_bytes() for p in units.iterdir()}
    with pytest.raises(ValueError,match='re-enrollment'):m.install(credential,'all')
    assert all(p.read_bytes()==data for p,data in before.items())


def test_root_management_account_is_used_for_host_business(setup,monkeypatch):
    credential,_,_,_=setup
    monkeypatch.setattr(os,'chown',lambda *args:None)
    result=m.install({**credential,'business_user':'root','business_uid':0,'business_gid':0},'0')
    value=json.loads(Path(result['config_paths'][0]).read_text())
    assert value['host_execution']=={'approved':True,'user':'root','uid':0,'gid':0,'allow_root_business':True}


@pytest.mark.parametrize('kind',['gpu','npu'])
def test_custom_workspace_root_creates_machine_and_device_directories(setup,tmp_path,kind):
    credential,_,_,_=setup
    base=tmp_path/'shared'
    preserved=base/'test'/'assets'
    preserved.mkdir(parents=True)
    marker=preserved/'keep.txt';marker.write_text('existing asset')
    result=m.install({**credential,'workspace_root':str(base),'accelerator_type':kind},'all')
    values=[json.loads(Path(path).read_text()) for path in result['config_paths']]
    assert {value['workspace_root'] for value in values}=={str(base/'test'/f'{kind}{index}') for index in (0,1)}
    assert {value['assets_root'] for value in values}=={str(base/'test'/'assets')}
    assert {value['cache_root'] for value in values}=={str(base/'test'/'cache')}
    assert all(not (Path(value['workspace_root'])/'assets').exists() for value in values)
    for value in values:
        for key in ('workspace_root','assets_root','cache_root'):
            assert Path(value[key]).is_dir()
        assert (Path(value['workspace_root'])/'runs').is_dir()
    assert marker.read_text()=='existing asset'


def test_shared_directories_pass_worker_validation_and_preserve_cache(setup,tmp_path):
    from worker.workspace import validate_storage
    credential,_,_,_=setup
    base=tmp_path/'shared'
    cache=base/'test'/'cache';cache.mkdir(parents=True)
    cached=cache/'model';cached.write_text('cached data')
    old_mode=cache.stat().st_mode
    result=m.install({**credential,'workspace_root':str(base)},'all')
    for path in result['config_paths']:
        value=json.loads(Path(path).read_text())
        value['min_free_disk_gb']=0
        validate_storage(value,value['state_path'],path)
    assert cached.read_text()=='cached data'
    assert cache.stat().st_mode==old_mode


def test_shared_asset_symlink_is_rejected_before_services_start(setup,tmp_path):
    credential,calls,_,_=setup
    machine=tmp_path/'shared'/'test';machine.mkdir(parents=True)
    target=tmp_path/'assets';target.mkdir()
    (machine/'assets').symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):
        m.install({**credential,'workspace_root':str(machine.parent)},'all')
    assert ['worker','start'] not in calls


@pytest.mark.parametrize('value',['relative/work','/root/private','/tmp/../escape'])
def test_invalid_workspace_root_rejected_before_installation(setup,value):
    credential,calls,control,_=setup
    with pytest.raises(ValueError):m.install({**credential,'workspace_root':value},'all')
    assert ['worker','start'] not in calls
    assert not control.exists()


def test_gpu_sharing_and_both_execution_modes_are_installed(setup):
    credential,calls,_,_=setup
    result=m.install({**credential,'execution_modes':['host','docker'],'gpu_sharing':'enabled'},'all')
    assert ['docker','info'] in calls
    for path in result['config_paths']:
        config=json.loads(Path(path).read_text())
        assert config['execution_modes']==['host','docker']
        assert config['gpu_runtime']['allow_shared'] is True


def test_failure_rolls_back_new_units_but_retains_business_data(setup,monkeypatch):
    credential,calls,control,units=setup
    def fail(layout,system):
        (units/'mini-eval-agent.service').write_text('partial')
        m.UPDATER_ROOT.mkdir()
        raise RuntimeError('injected updater failure')
    monkeypatch.setattr('scripts.install_node.install_updater',fail)
    with pytest.raises(RuntimeError,match='injected'):m.install(credential,'all')
    assert not control.exists() and not list(units.iterdir()) and not m.UPDATER_ROOT.exists()
    assert (m.WORKSPACES/'test/cache').is_dir()
    assert any(c[:3]==['systemctl','disable','--now'] for c in calls)


def test_failed_cleanup_preserves_recovery_evidence(setup,monkeypatch):
    credential,calls,control,units=setup
    def fail(layout,system):raise RuntimeError('install failure')
    monkeypatch.setattr('scripts.install_node.install_updater',fail)
    def stop_failed(args,**kw):
        if 'disable' in args:raise m.subprocess.CalledProcessError(1,args)
        return N(returncode=0)
    monkeypatch.setattr(m.subprocess,'run',stop_failed)
    with pytest.raises(m.subprocess.CalledProcessError):m.install(credential,'all')
    assert control.exists() and list(units.iterdir())

@pytest.mark.parametrize('case',['updater','agent','bad-instance','bad-kind','selection','duplicate','unit','symlink','nonroot'])
def test_invalid_installation_never_starts_services(setup,monkeypatch,case):
    credential,calls,control,units=setup;instance='gpu';selection='all'
    if case=='updater':m.UPDATER_ROOT.mkdir()
    if case=='agent':(units/'mini-eval-agent.service').write_text('existing')
    if case=='bad-instance':instance='../escape'
    if case=='bad-kind':credential['accelerator_type']='auto'
    if case in ('selection','duplicate'):
        credential['accelerator_type']='npu';selection='9' if case=='selection' else '0,0'
    if case=='unit':(units/'mini-eval-worker-managed-gpu-gpu1.service').write_text('existing')
    if case=='symlink':control.symlink_to(units,target_is_directory=True)
    if case=='nonroot':monkeypatch.setattr(os,'geteuid',lambda:1000)
    with pytest.raises(ValueError):m.install(credential,selection,instance)
    assert ['worker','start'] not in calls
    if case=='agent':assert (units/'mini-eval-agent.service').read_text()=='existing'


def test_docker_prerequisite_checks(setup):
    credential,calls,*_=setup;credential['execution_modes']=['docker']
    m.install(credential,'all')
    assert ['docker','info'] in calls
    assert not any(c[:2]==['docker','image'] or c[:2]==['docker','pull'] or c[:2]==['docker','build'] for c in calls)


def test_npu_sharing_requires_explicit_installer_choice(setup):
    credential,_,_,_=setup
    result=m.install({**credential,'accelerator_type':'npu','npu_sharing':'enabled'},'all')
    assert all(json.loads(Path(p).read_text())['npu_runtime']['allow_shared'] is True for p in result['config_paths'])

@pytest.mark.parametrize('user,uid,gid',[
    ('root',1,0),('root',0,1),('missing',1000,1000),('operator',1001,1001),
])
def test_business_account_conflicts_are_rejected(setup,user,uid,gid):
    credential,calls,control,_=setup
    with pytest.raises(ValueError):m.install({**credential,'business_user':user,'business_uid':uid,'business_gid':gid},'all')
    assert not control.exists()
    assert ['worker','start'] not in calls


def test_discover_npu_parses_physical_indices(monkeypatch):
    mapping='0 0 0 Ascend 910B\n'
    info='HBM-Usage(MB) AICore(%)\n| 0 910B | OK | 0 |\n| 0 | 0000:01:00.0 | 0 0/100 0/65536 |\n'
    monkeypatch.setattr(m.subprocess,'run',lambda args,**kw:N(stdout=mapping if '-m' in args else info))
    assert m.discover_npu()==[0]

@pytest.mark.parametrize('kind',['gpu','npu'])
def test_docker_only_never_calls_host_resource_or_device_probe(setup,monkeypatch,kind):
    credential,*_=setup
    def forbidden(*a):raise AssertionError('Docker-only must not require host systemd execution')
    monkeypatch.setattr('worker.host_identity.verify_system',forbidden)
    monkeypatch.setattr('worker.npu_host.probe',forbidden)
    result=m.install({**credential,'accelerator_type':kind,'execution_modes':['docker']},'all')
    assert len(result['config_paths'])==2
