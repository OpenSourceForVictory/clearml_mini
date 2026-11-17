"""Managed identity contract plus opt-in real systemd execution under a separate UID."""
import json
import os
from pathlib import Path
import pwd
import shutil
import tempfile
import time
from types import SimpleNamespace as N
import pytest
from worker import host_identity
from scripts import install_system_node
from backend.tests.test_host_backend import worker
from worker.host_backend import HostBackend


def test_root_worker_cannot_default_to_root(monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:0)
    with pytest.raises(RuntimeError,match='approved'):host_identity.settings({})
    monkeypatch.setattr(pwd,'getpwnam',lambda _:N(pw_uid=0,pw_gid=0,pw_name='root'))
    with pytest.raises(RuntimeError,match='must not run as root'):
        host_identity.settings({'host_execution':{'approved':True,'user':'root'}})


def test_identity_drift_and_unprivileged_manager(monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:0)
    monkeypatch.setattr(pwd,'getpwnam',lambda _:N(pw_uid=29001,pw_gid=29000,pw_name='mini-eval'))
    c={'host_execution':{'approved':True,'user':'mini-eval','uid':29000,'gid':29000}}
    with pytest.raises(RuntimeError,match='changed'):host_identity.settings(c)
    monkeypatch.setattr(os,'geteuid',lambda:1000)
    with pytest.raises(RuntimeError,match='root system Worker'):host_identity.settings(c)


def test_account_conflict_never_mutates_users(monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:0)
    def missing(_):raise KeyError()
    monkeypatch.setattr(install_system_node.grp,'getgrnam',missing)
    monkeypatch.setattr(pwd,'getpwnam',missing)
    monkeypatch.setattr(pwd,'getpwuid',lambda _:N(pw_name='another-user'))
    monkeypatch.setattr(install_system_node.subprocess,'run',lambda *a,**k:pytest.fail('account mutated before validation'))
    with pytest.raises(ValueError,match='another account'):install_system_node.account()


def test_onboarding_identity_fields_are_bounded():
    from onboarding.contracts import validate,Rejected
    from backend.tests.test_ssh_onboarding import form
    f=validate({**form(),'username':'root','accelerator_type':'npu'})
    assert f['business_uid']==29000 and f['business_gid']==29000
    for override in ({'business_uid':0},{'business_gid':True},{'accelerator_type':'auto'},{'business_user':'root'}):
        with pytest.raises(Rejected):validate({**form(),**override})


real=pytest.mark.skipif(os.getenv('MANAGED_HOST_TEST')!='1' or os.geteuid()!=0,reason='requires root, systemd and provisioned mini-eval test account')

@real
def test_real_business_permission_denied_before_launch():
    acc=pwd.getpwnam('mini-eval')
    with tempfile.TemporaryDirectory(prefix='mini-eval-private-',dir='/var/tmp') as d:
        Path(d).chmod(0o700)
        with pytest.raises(RuntimeError,match='mini-eval.*code root'):
            host_identity.check_paths(acc,[('code root',d,os.R_OK|os.X_OK)])


@real
@pytest.mark.parametrize('mode',['direct','sdk-command','command','service'])
def test_real_managed_identity_cwd_and_cleanup(mode,monkeypatch):
    acc=pwd.getpwnam('mini-eval')
    with tempfile.TemporaryDirectory(prefix='mini-eval-managed-',dir='/var/tmp') as d:
        root=Path(d);root.chmod(0o755)
        platform=root/'platform';platform.mkdir(mode=0o755)
        source=Path(__file__).resolve().parents[2]
        for package in ('worker','runner'):
            shutil.copytree(source/package,platform/package,ignore=shutil.ignore_patterns('__pycache__'))
        for path in platform.rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)
        monkeypatch.setattr('worker.host_backend.__file__',str(platform/'worker/host_backend.py'))
        business=root/'business';business.mkdir();business.chmod(0o755)
        (business/'settings.txt').write_text('relative');(business/'settings.txt').chmod(0o644)
        (business/'app.py').write_text('''import json,os,sys,time
from pathlib import Path
assert Path('settings.txt').read_text()=='relative'
assert os.getuid()!=0
out=Path(os.environ['MINI_EVAL_OUTPUT_DIR'])
out.joinpath('observed.json').write_text(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cwd':str(Path.cwd()),'python':sys.executable}))
if 'MINI_EVAL_RESULT' in os.environ:Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True}))
else:print(json.dumps({'final':{'success_rate':1.,'episode_count':2,'runtime':.01}}),flush=True)
''');(business/'app.py').chmod(0o644)
        w=worker(root,['python3','app.py']);w.config['host_execution']={'approved':True,'user':'mini-eval','uid':acc.pw_uid,'gid':acc.pw_gid}
        os.chown(root/'cache',acc.pw_uid,acc.pw_gid)
        task=w.state['task'];template=task['client_spec']['template'];template['host_runtime']['working_directory']='business'
        task['client_spec']['service_mode']='none';template['integration_mode']=mode if mode not in ('command','service') else 'direct'
        if mode=='command':template['task_type']='command'
        if mode=='service':task['client_spec'].update(workload='service',service={'port':19298,'address':'127.0.0.1'})
        backend=HostBackend(w)
        try:
            backend.create(task);backend.start(task)
            output=Path(w.workspace)/'runs'/task['execution_id']/'output/observed.json'
            deadline=time.monotonic()+15
            while not output.exists() and time.monotonic()<deadline:time.sleep(.1)
            assert output.exists(),backend.logs()
            record=json.loads(output.read_text());assert record['uid']==acc.pw_uid and record['gid']==acc.pw_gid
            assert record['cwd']==str(business) and record['python']=='/usr/bin/python3'
            until=time.monotonic()+10
            while backend.inspect(task['execution_id'])['State']['Running'] and time.monotonic()<until:time.sleep(.1)
            assert backend.inspect(task['execution_id'])['State']['ExitCode']==0,backend.logs()
        finally:backend.remove(task['execution_id'])
        assert not (backend.root/'launch.json').exists()

@pytest.mark.parametrize('policy,message',[
    ({'approved':True},'missing'),({'approved':True,'user':'missing'},'does not exist'),
])
def test_missing_business_account(monkeypatch,policy,message):
    monkeypatch.setattr(os,'geteuid',lambda:0)
    def missing(_):raise KeyError()
    monkeypatch.setattr(pwd,'getpwnam',missing)
    with pytest.raises(RuntimeError,match=message):host_identity.settings({'host_execution':policy})


def test_path_check_drops_groups_and_reports_failure(monkeypatch):
    account=N(pw_uid=29000,pw_gid=29000,pw_name='mini-eval',pw_dir='/business')
    monkeypatch.setattr(os,'getgrouplist',lambda *a:[29000,100])
    calls=[]
    def run(args,**kw):calls.append(kw);return N(returncode=1,stdout='bad probe output')
    monkeypatch.setattr(host_identity.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='permission check failed'):host_identity.check_paths(account,[('code','/private',5)])
    assert calls[0]['extra_groups']==[29000,100] and calls[0]['user']==29000


@pytest.mark.parametrize('case',['nonroot','cgroup','failure','success'])
def test_system_probe_bounded_cleanup(monkeypatch,case):
    monkeypatch.setattr(os,'geteuid',lambda:1000 if case=='nonroot' else 0)
    monkeypatch.setattr(Path,'exists',lambda _:case!='cgroup')
    calls=[]
    def run(args,**kw):
        calls.append(args)
        if case=='failure' and args[0]=='systemd-run':raise OSError('unavailable')
        return N(returncode=0)
    monkeypatch.setattr(host_identity.subprocess,'run',run)
    acc=N(pw_name='mini-eval',pw_gid=29000)
    if case in ('success','cgroup'):host_identity.verify_system(acc,source='pass')
    else:
        with pytest.raises(RuntimeError):host_identity.verify_system(acc,source='pass')
    if case in ('success','failure','cgroup'):
        assert '--property=User=mini-eval' in calls[0]
        assert [a[1] for a in calls[1:]]==['stop','reset-failed']
    else:assert not calls
