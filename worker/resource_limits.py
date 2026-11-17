"""Bounded host resource verification; safe to call from a background probe."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

CGROUP_ROOT = Path('/sys/fs/cgroup')
REQUIRED = {'cpu', 'memory', 'pids'}


def environment():
    uid = os.getuid()
    return {**os.environ, 'XDG_RUNTIME_DIR': f'/run/user/{uid}',
            'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'}


def run(argv, check=True):
    return subprocess.run(argv, env=environment(), capture_output=True, text=True,
                          timeout=15, check=check)


def inspect_host():
    result = {'ready': False, 'uid': os.getuid(), 'reason': None}
    if not (CGROUP_ROOT/'cgroup.controllers').is_file():
        return {**result, 'reason': 'cgroup_v2_required', 'repairable': False}
    try:
        group = run(['systemctl', '--user', 'show', '--property=ControlGroup', '--value']).stdout.strip()
        if not group.startswith('/') or group == '/':
            raise ValueError('invalid_user_cgroup')
        delegated = set((CGROUP_ROOT/group.lstrip('/')/'cgroup.controllers').read_text().split())
        if not REQUIRED <= delegated:
            return {**result, 'reason': 'controllers_not_delegated', 'repairable': True,
                    'missing': sorted(REQUIRED-delegated)}
        return {**result, 'ready': True, 'repairable': False}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {**result, 'reason': 'systemd_user_manager_unavailable', 'repairable': False}


def verify_current(cpus, memory_bytes, pids=256):
    """Read kernel limits in the task itself, before invoking business code."""
    groups={}
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        _,controllers,group=line.split(':',2)
        for controller in controllers.split(','):groups[controller]=group
    if '' in groups and (CGROUP_ROOT/'cgroup.controllers').is_file():
        verify_group(groups[''],cpus,memory_bytes,pids)
    else:
        verify_group(groups.get('cpu',''),cpus,memory_bytes,pids,groups=groups)


def verify_group(group,cpus,memory_bytes,pids=256,groups=None):
    if not group.startswith('/'):raise ValueError('missing task cgroup')
    if (CGROUP_ROOT/'cgroup.controllers').is_file():
        root=CGROUP_ROOT/group.lstrip('/')
        quota,period=(root/'cpu.max').read_text().split()
        memory=(root/'memory.max').read_text().strip()
        tasks=(root/'pids.max').read_text().strip()
    else:
        groups=groups or dict.fromkeys(('cpu','memory','pids'),group)
        def read(controller,name):
            path=groups.get(controller,'')
            if not path.startswith('/'):raise ValueError('missing '+controller+' cgroup')
            # v1 controllers may use combined mounts and different hierarchy paths.
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                left, sep, right = line.partition(' - ')
                fields=left.split(); tail=right.split()
                if not sep or len(fields)<5 or len(tail)<3 or tail[0]!='cgroup' or controller not in tail[2].split(','):continue
                mount=Path(fields[4]); root=fields[3]
                if not mount.is_relative_to(CGROUP_ROOT):continue
                if '..' in Path(path).parts:raise ValueError('invalid cgroup path')
                if path=='/':relative=''
                elif root=='/':relative=path.lstrip('/')
                elif path==root:relative=''
                elif path.startswith(root.rstrip('/')+'/'):relative=path[len(root):].lstrip('/')
                else:continue
                return (mount/relative/name).read_text().strip()
            if '..' in Path(path).parts:raise ValueError('invalid cgroup path')
            return (CGROUP_ROOT/controller/path.lstrip('/')/name).read_text().strip()
        quota=read('cpu','cpu.cfs_quota_us');period=read('cpu','cpu.cfs_period_us')
        memory=read('memory','memory.limit_in_bytes');tasks=read('pids','pids.max')
    if quota=='max' or int(quota)<=0 or int(quota)>cpus*int(period):raise ValueError('cpu_limit_not_enforced')
    if memory=='max' or not 0<int(memory)<=memory_bytes:raise ValueError('memory_limit_not_enforced')
    if tasks=='max' or not 0<int(tasks)<=pids:raise ValueError('pids_limit_not_enforced')


def verify_host(source=None):
    result = inspect_host()
    if not result['ready']:
        return result
    unit = 'mini-eval-resource-probe-'+uuid.uuid4().hex+'.service'
    try:
        # Run this source standalone: the installation bundle need not yet be installed.
        command = ['systemd-run', '--user', '--quiet', '--wait', '--pipe', '--unit='+unit,
                   '--property=CPUQuota=100%', '--property=MemoryMax=128M',
                   '--property=TasksMax=32', '--property=RuntimeMaxSec=10',
                   sys.executable, '-c', source or Path(__file__).read_text(), 'verify-current']
        run(command)
        return {**result, 'verified': True}
    except (OSError, subprocess.SubprocessError):
        return {**result, 'ready': False, 'verified': False,
                'reason': 'runtime_limits_not_enforced', 'repairable': True}
    finally:
        for action in ('stop', 'reset-failed'):
            try: run(['systemctl', '--user', action, unit], check=False)
            except (OSError, subprocess.SubprocessError): pass


if __name__ == '__main__':
    import json
    if sys.argv[-1] == 'verify-current':
        verify_current(1, 128*1024**2, 32)
    else:
        print(json.dumps(verify_host()))
