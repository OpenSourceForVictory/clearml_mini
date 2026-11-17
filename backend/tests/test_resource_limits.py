from pathlib import Path
from types import SimpleNamespace
import subprocess
import pytest
from worker import resource_limits as limits
from scripts import configure_node_resources as setup


def test_v1_never_claims_host_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    assert limits.verify_host()['reason']=='cgroup_v2_required'


def test_unlimited_task_checks_pids_without_requiring_cpu_or_memory_quota(tmp_path,monkeypatch):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    (tmp_path/'cgroup.controllers').write_text('cpu memory pids')
    group=tmp_path/'job';group.mkdir()
    (group/'cpu.max').write_text('max 100000')
    (group/'memory.max').write_text('max')
    (group/'pids.max').write_text('256')
    limits.verify_group('/job',0,0)
    with pytest.raises(ValueError,match='cpu_limit_not_enforced'):
        limits.verify_group('/job',1,0)
    with pytest.raises(ValueError,match='memory_limit_not_enforced'):
        limits.verify_group('/job',0,1024)
    (group/'pids.max').write_text('max')
    with pytest.raises(ValueError,match='pids_limit_not_enforced'):
        limits.verify_group('/job',0,0)


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


def test_hybrid_combined_controller_mounts(tmp_path,monkeypatch):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    original=Path.read_text
    cgroups='0::/unified-other\n2:cpu,cpuacct:/cpu-job\n3:memory:/mem-job\n4:pids:/pid-job\n'
    mountinfo=[]
    for number,controller,group,files in [
        (2,'cpu,cpuacct','cpu-job',{'cpu.cfs_quota_us':'100000','cpu.cfs_period_us':'100000'}),
        (3,'memory','mem-job',{'memory.limit_in_bytes':'134217728'}),
        (4,'pids','pid-job',{'pids.max':'32'})]:
        mount=tmp_path/controller;root=mount/group;root.mkdir(parents=True)
        for key,value in files.items():(root/key).write_text(value)
        mountinfo.append(f'{number} 1 0:1 / {mount} rw - cgroup cgroup rw,{controller}')
    def read(path,*args,**kw):
        if str(path)=='/proc/self/cgroup':return cgroups
        if str(path)=='/proc/self/mountinfo':return '\n'.join(mountinfo)
        return original(path,*args,**kw)
    monkeypatch.setattr(Path,'read_text',read)
    limits.verify_current(1,134217728,32)
    (tmp_path/'memory/mem-job/memory.limit_in_bytes').write_text('999999999')
    with pytest.raises(ValueError,match='memory_limit_not_enforced'):limits.verify_current(1,134217728,32)


def test_system_verification_not_rejected_just_for_v1(monkeypatch):
    from worker import host_identity
    monkeypatch.setattr(host_identity.os,'geteuid',lambda:0)
    monkeypatch.setattr(Path,'exists',lambda _:False)
    calls=[]
    monkeypatch.setattr(host_identity.subprocess,'run',lambda args,**kw:calls.append(args))
    host_identity.verify_system(source='probe')
    assert calls[0][0]=='systemd-run'
    assert '--property=MemoryMax=128M' in calls[0]

@pytest.mark.parametrize('group,mount_root,relative',[('/','/docker/id',''),('/docker/id','/docker/id',''),('/docker/id/task','/docker/id','task')])
def test_v1_namespace_mount_roots(tmp_path,monkeypatch,group,mount_root,relative):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    original=Path.read_text
    mounts=[]
    for controller,files in {'cpu':{'cpu.cfs_quota_us':'100000','cpu.cfs_period_us':'100000'},'memory':{'memory.limit_in_bytes':'134217728'},'pids':{'pids.max':'32'}}.items():
        mount=tmp_path/controller;directory=mount/relative;directory.mkdir(parents=True)
        for name,value in files.items():(directory/name).write_text(value)
        mounts.append(f'1 0 0:1 {mount_root} {mount} rw - cgroup cgroup rw,{controller}')
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**k:chr(10).join(mounts) if str(p)=='/proc/self/mountinfo' else original(p,*a,**k))
    limits.verify_group(group,1,134217728,32)

@pytest.mark.parametrize('group', ['relative','/../escape'])
def test_invalid_group_paths_rejected(tmp_path,monkeypatch,group):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    with pytest.raises(ValueError):limits.verify_group(group,1,134217728,32)

def test_missing_v1_controller_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    with pytest.raises(ValueError,match='missing cpu'):limits.verify_group('/unit',1,134217728,32,groups={'memory':'/unit'})

@pytest.mark.parametrize('bad', [None,'cpu','memory','pids'])
def test_v2_current_limits(tmp_path,monkeypatch,bad):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path)
    (tmp_path/'cgroup.controllers').write_text('cpu memory pids')
    root=tmp_path/'unit';root.mkdir()
    for key,value in {'cpu.max':'max 100000' if bad=='cpu' else '100000 100000','memory.max':'max' if bad=='memory' else '134217728','pids.max':'max' if bad=='pids' else '32'}.items():(root/key).write_text(value)
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**k:'0::/unit' if str(p)=='/proc/self/cgroup' else original(p,*a,**k))
    if bad:
        with pytest.raises(ValueError):limits.verify_current(1,134217728,32)
    else:limits.verify_current(1,134217728,32)

@pytest.mark.parametrize('group',['/','/user'])
def test_user_manager_invalid_or_inaccessible_group(tmp_path,monkeypatch,group):
    monkeypatch.setattr(limits,'CGROUP_ROOT',tmp_path);(tmp_path/'cgroup.controllers').touch()
    monkeypatch.setattr(limits,'run',lambda *a,**k:SimpleNamespace(stdout=group))
    assert limits.inspect_host()['reason']=='systemd_user_manager_unavailable'

def test_probe_cleanup_failure_is_bounded(monkeypatch):
    monkeypatch.setattr(limits,'inspect_host',lambda:{'ready':True})
    def fail(*a,**k):raise OSError('unavailable')
    monkeypatch.setattr(limits,'run',fail)
    assert limits.verify_host()['reason']=='runtime_limits_not_enforced'

def test_resource_command_passes_user_bus_and_timeout(monkeypatch):
    monkeypatch.setattr(limits.os,'getuid',lambda:1001)
    calls=[]
    monkeypatch.setattr(limits.subprocess,'run',lambda *a,**kw:calls.append(kw))
    limits.run(['true'])
    assert calls[0]['timeout']==15
    assert calls[0]['env']['DBUS_SESSION_BUS_ADDRESS']=='unix:path=/run/user/1001/bus'
