from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest
from worker import resource_limits as limits
from scripts import configure_node_resources as setup


def test_v1_never_claims_host_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    assert limits.verify_host()['reason']=='cgroup_v2_required'


def test_missing_delegation_is_repairable(tmp_path, monkeypatch):
    (tmp_path/'cgroup.controllers').write_text('cpu memory pids')
    (tmp_path/'user').mkdir();(tmp_path/'user/cgroup.controllers').write_text('memory pids')
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    monkeypatch.setattr(limits,'run',lambda *a,**k:SimpleNamespace(stdout='/user'))
    result=limits.inspect_host()
    assert not result['ready'] and result['repairable'] and result['missing']==['cpu']


@pytest.mark.parametrize('failed',[False,True])
def test_probe_always_cleans_owned_unit_and_checks_real_execution(monkeypatch,failed):
    monkeypatch.setattr(limits,'inspect_host',lambda:{'ready':True})
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        if argv[0]=='systemd-run' and failed:raise subprocess.CalledProcessError(1,argv)
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(limits,'run',run)
    result=limits.verify_host()
    assert result['ready'] is not failed
    assert '--property=CPUQuota=100%' in calls[0]
    assert calls[-2][2]=='stop' and calls[-1][2]=='reset-failed'
    assert calls[-2][-1]==calls[-1][-1]


def test_delegation_is_idempotent_and_never_restarts(tmp_path,monkeypatch):
    monkeypatch.setattr(setup.os,'geteuid',lambda:0)
    monkeypatch.setattr(setup.pwd,'getpwuid',lambda uid:SimpleNamespace(pw_name='worker',pw_shell='/bin/bash'))
    original=Path.is_file
    monkeypatch.setattr(Path,'is_file',lambda p:True if str(p)=='/sys/fs/cgroup/cgroup.controllers' else original(p))
    calls=[];monkeypatch.setattr(setup.subprocess,'run',lambda argv,**kw:calls.append(argv))
    path=setup.configure(1001,tmp_path);before=path.stat().st_mtime_ns
    setup.configure(1001,tmp_path)
    assert path.stat().st_mtime_ns==before
    assert 'Delegate=cpu memory pids' in path.read_text()
    assert all('restart' not in argv and 'reboot' not in argv for argv in calls)
    path.write_text('administrator override')
    with pytest.raises(ValueError,match='differs'):setup.configure(1001,tmp_path)
    assert path.read_text()=='administrator override'


def test_unprivileged_configuration_and_root_task_user_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(setup.os,'geteuid',lambda:1001)
    with pytest.raises(PermissionError):setup.configure(1001,tmp_path)
    monkeypatch.setattr(setup.os,'geteuid',lambda:0)
    with pytest.raises(ValueError):setup.configure(0,tmp_path)


@pytest.mark.parametrize('quota,memory,pids,valid',[(100000,134217728,32,True),(-1,134217728,32,False),(200000,134217728,32,False),(100000,268435456,32,False),(100000,134217728,'max',False)])
def test_system_scope_cgroup_v1_limits(tmp_path,monkeypatch,quota,memory,pids,valid):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    for controller,values in {'cpu':{'cpu.cfs_quota_us':quota,'cpu.cfs_period_us':100000},'memory':{'memory.limit_in_bytes':memory},'pids':{'pids.max':pids}}.items():
        root=tmp_path/controller/'unit';root.mkdir(parents=True)
        for name,value in values.items():(root/name).write_text(str(value))
    if valid:limits.verify_group('/unit',1,134217728,32)
    else:
        with pytest.raises(ValueError):limits.verify_group('/unit',1,134217728,32)
