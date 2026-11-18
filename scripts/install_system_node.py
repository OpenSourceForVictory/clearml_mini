"""New root-managed installation. Existing identities require explicit re-enrollment."""
import fcntl
import re
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

CONTROL=Path('/var/lib/mini-eval/control')
WORKSPACES=Path('/var/lib/mini-eval/workspaces')
UNIT_DIR=Path('/etc/systemd/system')
INSTALL_LOCK=Path('/run/lock/mini-eval-system-install.lock')
UPDATER_ROOT=Path('/opt/mini-eval-updater')


def approved_account(credential):
    user=credential.get('business_user') or 'root'
    if not isinstance(user,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*',user):
        raise ValueError('approved business account is required')
    try: acc=pwd.getpwnam(user)
    except KeyError: raise ValueError('business account does not exist') from None
    if user == 'root':
        if int(credential.get('business_uid', 0)) != 0 or int(credential.get('business_gid', 0)) != 0:
            raise ValueError('root business identity must use UID/GID 0')
    elif acc.pw_uid==0:
        raise ValueError('root business identity must use root account')
    if 'business_uid' in credential and int(credential['business_uid'])!=acc.pw_uid:
        raise ValueError('business UID differs from the SSH account')
    if 'business_gid' in credential and int(credential['business_gid'])!=acc.pw_gid:
        raise ValueError('business GID differs from the SSH account')
    return acc


def workspace_base(credential):
    from worker.workspace import checked_path
    value=credential.get('workspace_root') or str(WORKSPACES)
    if not isinstance(value,str) or len(value)>4096:
        raise ValueError('invalid workspace root')
    return checked_path(value)


def discover_npu():
    from node_agent.ascend import mappings,metrics
    def command(args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=15).stdout
    mapping=mappings(command(['npu-smi','info','-m']));values=metrics(command(['npu-smi','info']),mapping)
    return sorted(v[0] for v in mapping.values())


def _install(credential,selection,instance='gpu'):
    from scripts.install_worker import identity,discover,plan
    from scripts.install_node import install_updater
    from worker.storage import atomic_json
    from worker.host_identity import verify_system,check_paths
    from worker.workspace import checked_path
    from worker.updater import Updater
    identity(credential)
    if os.geteuid()!=0:raise ValueError('root system installation requires root')
    # Do not reuse another installation or rewrite running services.
    if (CONTROL/'.local/share/mini-eval-worker').exists():raise ValueError('managed installation exists; use re-enrollment')
    if UPDATER_ROOT.exists():raise ValueError('existing updater installation requires explicit recovery')
    if any((UNIT_DIR/name).exists() for name in ('mini-eval-agent.service','mini-eval-updater.service')):raise ValueError('existing system node installation requires explicit migration')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',instance):raise ValueError('invalid instance name')
    kind=credential.get('accelerator_type','gpu')
    if kind not in ('gpu','npu'):raise ValueError('unsupported accelerator type')
    if credential.get('npu_sharing','preserve') not in ('preserve','enabled','disabled'):
        raise ValueError('invalid NPU sharing choice')
    if credential.get('gpu_sharing','preserve') not in ('preserve','enabled','disabled'):
        raise ValueError('invalid GPU sharing choice')
    if credential.get('execution_modes') != ['host']:
        subprocess.run(['docker','info'],check=True,capture_output=True,timeout=30)
    acc=approved_account(credential)
    base=workspace_base(credential)
    if 'host' in credential.get('execution_modes',['docker','host']):verify_system(acc)
    kind=credential.get('accelerator_type','gpu')
    if kind=='npu':
        available=discover_npu();indices=available if selection=='all' else [int(x) for x in selection.split(',')]
        if not indices or not set(indices)<=set(available):raise ValueError('selected NPU does not exist')
    else:indices=discover(credential,selection)
    if len(indices)!=len(set(indices)):raise ValueError('duplicate device index')
    node_name=credential['node_name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',node_name) or node_name in ('.','..'):
        raise ValueError('invalid node name for workspace')
    config={**credential,'host_execution':{'approved':True,'user':acc.pw_name,'uid':acc.pw_uid,'gid':acc.pw_gid,**({'allow_root_business':True} if acc.pw_uid==0 else {})}}
    if kind=='npu':
        config['npu_runtime']={'approved':True,'allow_shared':credential.get('npu_sharing')=='enabled','host':{'approved':True,'user':acc.pw_name,'environment':{}}}
    else:
        config['gpu_runtime']={'allow_shared':credential.get('gpu_sharing')=='enabled'}
    planned=plan(config,instance,selection,indices,CONTROL)
    for name,_,value in planned:
        workspace=base/node_name/(kind+str(value['gpu_index']))
        value.update(workspace_root=str(workspace),assets_root=str(base/node_name/'assets'),cache_root=str(base/node_name/'cache'))
        if (UNIT_DIR/('mini-eval-worker-managed-'+name+'.service')).exists():raise ValueError('managed Worker unit already exists')
        if kind=='npu' and 'host' in credential.get('execution_modes',['docker','host']):
            from worker.npu_host import probe
            probe(value)
    for path in (CONTROL,CONTROL/'.local',CONTROL/'.local/share'):
        if path.is_symlink():raise ValueError('managed installation path must not be a symlink')
        path.mkdir(parents=True,exist_ok=True,mode=0o755);path.chmod(0o755)
    for path in (CONTROL/'.config',CONTROL/'.config/mini-eval',CONTROL/'.local/state'):
        path.mkdir(parents=True,exist_ok=True,mode=0o700);path.chmod(0o700)
    app=CONTROL/'.local/share/mini-eval-worker';app.mkdir(mode=0o755)
    source=Path(__file__).resolve().parents[1]
    for package in ('worker','runner','node_agent'):
        shutil.copytree(source/package,app/package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for path in app.rglob('*'):path.chmod(0o755 if path.is_dir() else 0o644)
    (app/'.worker-release.json').write_text(json.dumps({'sha256':(source/'release.sha256').read_text().strip()}))
    layout={'app_dir':str(app),'state_dir':str(CONTROL/'.local/state/mini-eval-updater'),'config_paths':[],'services':[]}
    quote=lambda x:json.dumps(str(x).replace('%','%%'))
    for name,target,value in planned:
        # Probe both device identity and business directory access before enabling any service.
        workspace=checked_path(value['workspace_root'])
        create_workspace=not workspace.exists()
        workspace.mkdir(mode=0o711,parents=True,exist_ok=True)
        if create_workspace:os.chown(workspace,acc.pw_uid,acc.pw_gid)
        for directory,mode in ((value['assets_root'],0o755),(value['cache_root'],0o755),(workspace/'runs',0o700)):
            d=checked_path(directory)
            if not d.exists():
                d.mkdir(mode=mode)
                os.chown(d,acc.pw_uid,acc.pw_gid)
        check_paths(acc,[('platform code',str(app),os.R_OK|os.X_OK),
            ('workspace',str(workspace),os.R_OK|os.W_OK|os.X_OK),
            ('assets',value['assets_root'],os.R_OK|os.X_OK),
            ('cache',value['cache_root'],os.R_OK|os.W_OK|os.X_OK),
            ('runs',str(workspace/'runs'),os.R_OK|os.W_OK|os.X_OK)])
        atomic_json(target,value)
        unitname='mini-eval-worker-managed-'+name+'.service'
        unit=UNIT_DIR/unitname
        if unit.exists():raise ValueError('managed Worker unit already exists')
        unit.write_text('[Unit]\nDescription=Mini Eval managed Worker\nAfter=network-online.target\n[Service]\nType=simple\nUser=root\nWorkingDirectory='+str(app).replace('%','%%')+'\nExecStart='+quote(sys.executable)+' -m worker.client --config '+quote(target)+'\nRestart=always\nRestartSec=5\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n')
        layout['config_paths'].append(str(target));layout['services'].append({'scope':'system','name':unitname})
    install_updater(layout,True)
    updater=Updater(layout);updater.control('enable');updater.control('start')
    return layout


def install(credential,selection,instance='gpu'):
    """Serialize installation; preserve evidence and stop partial services on failure.

    Retain the business account and workspaces so retry cannot change NFS ownership.
    Only remove control files and units created by this invocation, never existing data.
    """
    if os.geteuid()!=0:raise ValueError('root system installation requires root')
    with INSTALL_LOCK.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        existing_control=CONTROL.exists()
        existing_updater=UPDATER_ROOT.exists()
        before={p.name for p in UNIT_DIR.glob('mini-eval-*.service')}
        try:return _install(credential,selection,instance)
        except Exception:
            # An existing installation belongs to its recovery workflow, never this rollback.
            if not existing_control:
                created=[p for p in UNIT_DIR.glob('mini-eval-*.service') if p.name not in before]
                for p in created:
                    subprocess.run(['systemctl','disable','--now',p.name],capture_output=True,timeout=40,check=True)
                for p in created:p.unlink()
                subprocess.run(['systemctl','daemon-reload'],capture_output=True,timeout=30,check=True)
                if CONTROL.exists():shutil.rmtree(CONTROL)
                if not existing_updater and UPDATER_ROOT.exists():shutil.rmtree(UPDATER_ROOT)
            raise
