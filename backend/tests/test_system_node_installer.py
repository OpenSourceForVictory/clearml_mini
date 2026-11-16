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
    acc=N(pw_name='mini-eval',pw_uid=os.getuid(),pw_gid=os.getgid(),pw_dir=str(tmp_path/'business'))
    monkeypatch.setattr(m,'account',lambda *a:acc)
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
        assert value['host_execution']['user']=='mini-eval'
        assert Path(path).stat().st_mode&0o777==0o600
        assert (control/'.config').stat().st_mode&0o777==0o700
        assert service['scope']=='system'
        REAL_RUN(['systemd-analyze','verify','--man=no',str(units/service['name'])],check=True,capture_output=True)
        assert 'User=root\n' in (units/service['name']).read_text()
        assert '\nWorkingDirectory='+result['app_dir']+'\n' in (units/service['name']).read_text()
        assert (Path(value['workspace_root'])/'cache').stat().st_mode&0o777==0o700
        if kind=='npu':assert value['npu_runtime']['host']['user']=='mini-eval'
    assert calls[-2:]==[['worker','enable'],['worker','start']]
    before={p:p.read_bytes() for p in units.iterdir()}
    with pytest.raises(ValueError,match='re-enrollment'):m.install(credential,'all')
    assert all(p.read_bytes()==data for p,data in before.items())


def test_failure_rolls_back_new_units_but_retains_business_data(setup,monkeypatch):
    credential,calls,control,units=setup
    def fail(layout,system):
        (units/'mini-eval-agent.service').write_text('partial')
        m.UPDATER_ROOT.mkdir()
        raise RuntimeError('injected updater failure')
    monkeypatch.setattr('scripts.install_node.install_updater',fail)
    with pytest.raises(RuntimeError,match='injected'):m.install(credential,'all')
    assert not control.exists() and not list(units.iterdir()) and not m.UPDATER_ROOT.exists()
    assert (m.WORKSPACES/'gpu-gpu0/cache').is_dir()
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
    assert ['docker','info'] in calls and ['docker','image','inspect','test'] in calls

@pytest.mark.parametrize('issue',['uid','gid','home','privileged','range','nonroot'])
def test_account_rejection_before_mutation(monkeypatch,issue):
    monkeypatch.setattr(os,'geteuid',lambda:1000 if issue=='nonroot' else 0)
    user=N(pw_name='mini-eval',pw_uid=29001 if issue=='uid' else 29000,pw_gid=29000,pw_dir='wrong' if issue=='home' else str(m.BUSINESS_HOME))
    group=N(gr_name='mini-eval',gr_gid=29001 if issue=='gid' else 29000)
    monkeypatch.setattr(m.pwd,'getpwnam',lambda _:user);monkeypatch.setattr(m.grp,'getgrnam',lambda _:group)
    monkeypatch.setattr(m.pwd,'getpwuid',lambda _:user);monkeypatch.setattr(m.grp,'getgrgid',lambda _:group)
    monkeypatch.setattr(m.grp,'getgrall',lambda:[N(gr_name='sudo',gr_mem=['mini-eval'])] if issue=='privileged' else [])
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**k:pytest.fail('unexpected mutation'))
    with pytest.raises(ValueError):m.account(0 if issue=='range' else 29000)


def test_account_creation_then_idempotence(monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:0);calls=[];users={};groups={}
    user=N(pw_name='mini-eval',pw_uid=29000,pw_gid=29000,pw_dir=str(m.BUSINESS_HOME))
    group=N(gr_name='mini-eval',gr_gid=29000)
    monkeypatch.setattr(m.pwd,'getpwnam',lambda name:users[name]);monkeypatch.setattr(m.grp,'getgrnam',lambda name:groups[name])
    monkeypatch.setattr(m.pwd,'getpwuid',lambda _:users['mini-eval']);monkeypatch.setattr(m.grp,'getgrgid',lambda _:groups['mini-eval'])
    monkeypatch.setattr(m.grp,'getgrall',lambda:[])
    def run(args,**kw):
        calls.append(args)
        if args[0]=='groupadd':groups['mini-eval']=group
        if args[0]=='useradd':users['mini-eval']=user
    monkeypatch.setattr(m.subprocess,'run',run)
    assert m.account()==user
    assert '--shell' in calls[1] and '/usr/sbin/nologin' in calls[1]
    assert m.account()==user and len(calls)==2


def test_discover_npu_parses_physical_indices(monkeypatch):
    mapping='0 0 0 Ascend 910B\n'
    info='HBM-Usage(MB) AICore(%)\n| 0 910B | OK | 0 |\n| 0 | 0000:01:00.0 | 0 0/100 0/65536 |\n'
    monkeypatch.setattr(m.subprocess,'run',lambda args,**kw:N(stdout=mapping if '-m' in args else info))
    assert m.discover_npu()==[0]
