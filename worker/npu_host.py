"""System-managed Ascend host execution with kernel device access controls.

The privileged Worker manages units; business programs always run as a configured
non-root account. No task-supplied setting can widen the device allow list.
"""
import errno
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import uuid
from .accelerators import approved, COMMON

CANN_ENV_KEYS=frozenset({'PATH','LD_LIBRARY_PATH','PYTHONPATH','ASCEND_HOME_PATH',
                         'ASCEND_OPP_PATH','ASCEND_AICPU_PATH','ASCEND_TOOLKIT_HOME'})
CANN_SETUP_FILES=(Path('/usr/local/Ascend/cann/set_env.sh'),
                  Path('/usr/local/Ascend/ascend-toolkit/set_env.sh'))


def installed_environment(account, base):
    """Load the installed CANN setup as the business user, not the root Worker."""
    home=base.get('ASCEND_HOME_PATH','')
    candidates=([Path(home)/'set_env.sh'] if home and Path(home).is_absolute() else [])
    candidates += [Path(account.pw_dir)/'Ascend/cann/set_env.sh',*CANN_SETUP_FILES]
    script=next((path for path in candidates if path.is_file()),None)
    if script is None:return {}
    try:
        result=subprocess.run(
            ['/bin/bash','--noprofile','--norc','-c',
             'set -e; source "$1" >/dev/null; /usr/bin/env -0',
             'cann-setup',str(script)],
            env=base,cwd='/',user=account.pw_uid,group=account.pw_gid,
            extra_groups=os.getgrouplist(account.pw_name,account.pw_gid),
            capture_output=True,check=True,timeout=10)
    except (OSError,subprocess.SubprocessError) as exc:
        raise RuntimeError('installed CANN environment setup failed; check set_env.sh access and installation') from exc
    values={}
    for entry in result.stdout.split(b'\0'):
        if b'=' not in entry:continue
        key,value=entry.split(b'=',1)
        try:
            name=key.decode('ascii')
            if name in CANN_ENV_KEYS:values[name]=value.decode('utf-8')
        except UnicodeError as exc:
            raise RuntimeError('installed CANN environment contains invalid text') from exc
    if not values.get('LD_LIBRARY_PATH'):
        raise RuntimeError('installed CANN environment has no LD_LIBRARY_PATH')
    return values


def settings(config):
    profile,device=approved(config)
    host=profile.get('host')
    if not isinstance(host,dict) or host.get('approved') is not True:
        raise RuntimeError('NPU host execution requires node administrator approval')
    if os.geteuid()!=0:raise RuntimeError('NPU device isolation requires a system-managed privileged Worker')
    user=host.get('user')
    if not isinstance(user,str) or not user:raise RuntimeError('NPU host requires a non-root service account')
    account=pwd.getpwnam(user)
    if config.get('host_execution'):
        from .host_identity import settings as identity_settings
        if identity_settings(config).pw_uid!=account.pw_uid:raise RuntimeError('NPU and managed business identities differ')
    if account.pw_uid==0:raise RuntimeError('NPU business programs must not run as root')
    environment=host.get('environment',{})
    allowed={'PATH','LD_LIBRARY_PATH','PYTHONPATH','ASCEND_HOME_PATH','ASCEND_OPP_PATH','ASCEND_AICPU_PATH'}
    if not isinstance(environment,dict) or set(environment)-allowed or any(not isinstance(v,str) or '\x00' in v for v in environment.values()):
        raise RuntimeError('invalid administrator-approved CANN environment')
    return account,device,dict(environment)


def properties(config):
    account,device,_=settings(config)
    result=['User='+account.pw_name,'Group='+str(account.pw_gid),'DevicePolicy=closed',
            'NoNewPrivileges=yes','CapabilityBoundingSet=','RestrictSUIDSGID=yes']
    result+=['DeviceAllow='+path+' rw' for path in (device,*COMMON)]
    return result


def verify_devices(device):
    """Execute inside the restricted unit before launching user code."""
    for path in (device,):
        fd=os.open(path,os.O_RDWR|os.O_CLOEXEC);os.close(fd)
    for path in Path('/dev').glob('davinci[0-9]*'):
        if str(path)==device:continue
        try:fd=os.open(path,os.O_RDWR|os.O_CLOEXEC)
        except OSError as error:
            if error.errno not in (errno.EPERM,errno.EACCES):raise
        else:
            os.close(fd);raise RuntimeError('unassigned NPU device is accessible')


def probe(config):
    _,device,_=settings(config)
    unit='mini-eval-npu-probe-'+uuid.uuid4().hex+'.service'
    # Sources are embedded so an approved non-root account can probe before installation.
    resources=Path(__file__).with_name('resource_limits.py').read_text()
    import inspect
    check=inspect.getsource(verify_devices)
    program="import errno,os; from pathlib import Path; COMMON="+repr(COMMON)+"; scope={'__name__':'limits'}; exec("+repr(resources)+",scope); scope['verify_current'](1,128*1024**2,32); "+'\n'+check+'\nverify_devices('+repr(device)+')'
    args=['systemd-run','--quiet','--wait','--pipe','--unit='+unit,
          '--property=CPUQuota=100%','--property=MemoryMax=128M',
          '--property=TasksMax=32','--property=RuntimeMaxSec=10']
    args+=['--property='+value for value in properties(config)]
    try:
        subprocess.run([*args,'/usr/bin/python3','-c',program],check=True,capture_output=True,text=True,timeout=20)
    except subprocess.CalledProcessError as error:
        raise RuntimeError('NPU host isolation probe failed: '+error.stderr[-1000:]) from None
    finally:
        for action in ('stop','reset-failed'):
            subprocess.run(['systemctl',action,unit],capture_output=True,timeout=15)
