"""Node-approved business identity; never derived from task/template parameters."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import uuid


def settings(config):
    policy=config.get('host_execution')
    if not isinstance(policy,dict) or policy.get('approved') is not True:
        raise RuntimeError('root host execution requires an approved non-root business account')
    if os.geteuid()!=0:raise RuntimeError('managed host execution requires a root system Worker')
    user=policy.get('user')
    if not isinstance(user,str) or not user:raise RuntimeError('business account is missing')
    try:account=pwd.getpwnam(user)
    except KeyError:raise RuntimeError('business account does not exist: '+user) from None
    if account.pw_uid==0:raise RuntimeError('host business programs must not run as root')
    if policy.get('uid',account.pw_uid)!=account.pw_uid or policy.get('gid',account.pw_gid)!=account.pw_gid:
        raise RuntimeError('business account UID/GID changed; repeat node environment approval')
    return account


def properties(account):
    return ['User='+account.pw_name,'Group='+str(account.pw_gid),'NoNewPrivileges=yes',
            'CapabilityBoundingSet=','RestrictSUIDSGID=yes','UMask=0077']


def check_paths(account,paths):
    """Run actual DAC checks after dropping all root groups; no shell or business code."""
    program='''import json,os,sys
for label,path,mode in json.loads(sys.argv[1]):
    if not os.access(path,mode):
        print(json.dumps({'label':label,'path':path,'mode':mode}));sys.exit(1)
'''
    result=subprocess.run(['/usr/bin/python3','-c',program,json.dumps(paths)],cwd='/',
        user=account.pw_uid,group=account.pw_gid,extra_groups=os.getgrouplist(account.pw_name,account.pw_gid),
        env={'PATH':'/usr/bin:/bin','HOME':account.pw_dir},capture_output=True,text=True,timeout=10)
    if result.returncode:
        try:
            item=json.loads(result.stdout)
            raise RuntimeError(f"business user {account.pw_name} cannot access {item['label']}: {item['path']} (required mode {item['mode']}); grant traversal/read/write as appropriate")
        except (ValueError,KeyError):raise RuntimeError('business account permission check failed: '+account.pw_name) from None


def verify_system(account=None,source=None):
    """Check real system-unit limits, optionally under the business account."""
    if os.geteuid()!=0:raise RuntimeError('system-managed host execution requires root Worker')
    if not Path('/sys/fs/cgroup/cgroup.controllers').exists():raise RuntimeError('cgroup v2 is required for managed host execution')
    source=source or Path(__file__).with_name('resource_limits.py').read_text()
    unit='mini-eval-identity-probe-'+uuid.uuid4().hex+'.service'
    args=['systemd-run','--quiet','--wait','--pipe','--unit='+unit,
          '--property=CPUQuota=100%','--property=MemoryMax=128M','--property=TasksMax=32','--property=RuntimeMaxSec=10']
    if account:args+=['--property='+x for x in properties(account)]
    try:
        subprocess.run(args+['/usr/bin/python3','-c',source,'verify-current'],check=True,capture_output=True,text=True,timeout=20)
    except (OSError,subprocess.SubprocessError):raise RuntimeError('system host resource limits could not be verified') from None
    finally:
        for action in ('stop','reset-failed'):
            subprocess.run(['systemctl',action,unit],capture_output=True,timeout=15)
