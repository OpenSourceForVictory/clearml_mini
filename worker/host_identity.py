"""Node-approved business identity; never derived from task/template parameters."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import uuid

_ENV_MARKER=b'\0MINI_EVAL_LOGIN_ENV\0'


def login_environment(account, timeout_seconds=60):
    """Capture exported login variables under the approved business identity."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError('host_login_timeout_seconds must be an integer between 1 and 300')
    shell=account.pw_shell
    if not shell or not Path(shell).is_absolute() or not os.access(shell,os.X_OK):
        raise RuntimeError('business account login shell is unavailable')
    seed={'HOME':account.pw_dir,'USER':account.pw_name,'LOGNAME':account.pw_name,
          'SHELL':shell,'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','TERM':'dumb'}
    identity=({'user':account.pw_uid,'group':account.pw_gid,
               'extra_groups':os.getgrouplist(account.pw_name,account.pw_gid)}
              if os.geteuid()==0 else {})
    if os.geteuid()!=0 and os.geteuid()!=account.pw_uid:
        raise RuntimeError('business account login environment requires the approved identity')
    try:
        result=subprocess.run(['-'+Path(shell).name,'-c',
                               "printf '\\0MINI_EVAL_LOGIN_ENV\\0'; /usr/bin/env -0"],
            executable=shell,env=seed,cwd=account.pw_dir,stdin=subprocess.DEVNULL,
            capture_output=True,timeout=timeout_seconds,**identity)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f'business account login environment timed out after {timeout_seconds} seconds '
            f'(user: {account.pw_name}); check login startup scripts or node host_login_timeout_seconds'
        ) from None
    except (OSError,subprocess.SubprocessError) as exc:
        raise RuntimeError('business account login environment could not be loaded') from exc
    if result.returncode or _ENV_MARKER not in result.stdout:
        raise RuntimeError('business account login environment could not be loaded')
    raw=result.stdout.split(_ENV_MARKER,1)[1]
    if len(raw)>131072 or not raw.endswith(b'\0'):
        raise RuntimeError('business account login environment is invalid or too large')
    values={}
    for entry in raw[:-1].split(b'\0'):
        if b'=' not in entry:raise RuntimeError('business account login environment is invalid')
        key,value=entry.split(b'=',1)
        # Bash exports functions as environment entries, not ordinary variables.
        # Keep their exported effects (PATH etc.), never forward function bodies.
        if key.startswith(b'BASH_FUNC_') and key.endswith(b'%%'):
            continue
        if not key or len(value)>16384:
            raise RuntimeError('business account login environment is invalid')
        try:values[key.decode('utf-8')]=value.decode('utf-8')
        except UnicodeError as exc:
            raise RuntimeError('business account login environment is invalid') from exc
    return values


def settings(config):
    policy=config.get('host_execution')
    if not isinstance(policy,dict) or policy.get('approved') is not True:
        raise RuntimeError('root host execution requires an approved business account')
    if os.geteuid()!=0:raise RuntimeError('managed host execution requires a root system Worker')
    user=policy.get('user')
    if not isinstance(user,str) or not user:raise RuntimeError('business account is missing')
    try:account=pwd.getpwnam(user)
    except KeyError:raise RuntimeError('business account does not exist: '+user) from None
    if account.pw_uid==0:
        if (user!='root' or policy.get('allow_root_business') is not True
                or policy.get('uid')!=0 or policy.get('gid')!=0):
            raise RuntimeError('root business account requires explicit node approval')
    elif policy.get('allow_root_business') is True:
        raise RuntimeError('root business approval requires the root account')
    if policy.get('uid',account.pw_uid)!=account.pw_uid or policy.get('gid',account.pw_gid)!=account.pw_gid:
        raise RuntimeError('business account UID/GID changed; repeat node environment approval')
    return account


def properties(account):
    capabilities='CAP_DAC_OVERRIDE CAP_DAC_READ_SEARCH' if account.pw_uid==0 else ''
    return ['User='+account.pw_name,'Group='+str(account.pw_gid),'NoNewPrivileges=yes',
            'CapabilityBoundingSet='+capabilities,'RestrictSUIDSGID=yes','UMask=0077']


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
