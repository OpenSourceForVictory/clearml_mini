"""New root-managed installation. Existing identities require explicit re-enrollment."""
import fcntl
import re
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys

CONTROL=Path('/var/lib/mini-eval/control')
BUSINESS_HOME=Path('/var/lib/mini-eval/business')
WORKSPACES=Path('/var/lib/mini-eval/workspaces')
UNIT_DIR=Path('/etc/systemd/system')
INSTALL_LOCK=Path('/run/lock/mini-eval-system-install.lock')
UPDATER_ROOT=Path('/opt/mini-eval-updater')


def account(uid=29000,gid=29000):
    if os.geteuid()!=0:raise ValueError('root installation requires root')
    if not 1000<=uid<=60000 or not 1000<=gid<=60000:raise ValueError('business UID/GID must be between 1000 and 60000')
    try:g=grp.getgrnam('mini-eval')
    except KeyError:g=None
    try:u=pwd.getpwnam('mini-eval')
    except KeyError:u=None
    if g and g.gr_gid!=gid:raise ValueError('mini-eval GID differs; coordinate shared-storage identity first')
    if u and (u.pw_uid!=uid or u.pw_gid!=gid or u.pw_dir!=str(BUSINESS_HOME)):
        raise ValueError('mini-eval account differs; do not replace an existing account')
    for lookup,number,name in ((pwd.getpwuid,uid,'pw_name'),(grp.getgrgid,gid,'gr_name')):
        try:found=lookup(number)
        except KeyError:continue
        if getattr(found,name)!='mini-eval':raise ValueError('business UID/GID belongs to another account')
    if u and any(x.gr_name in ('sudo','wheel','docker','root') and u.pw_name in x.gr_mem for x in grp.getgrall()):
        raise ValueError('business account must not belong to privileged groups')
    if not g:subprocess.run(['groupadd','--gid',str(gid),'mini-eval'],check=True,capture_output=True)
    if not u:
        subprocess.run(['useradd','--uid',str(uid),'--gid',str(gid),'--home-dir',str(BUSINESS_HOME),
                        '--create-home','--shell','/usr/sbin/nologin','--password','!','mini-eval'],check=True,capture_output=True)
    return pwd.getpwnam('mini-eval')


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
    if credential.get('execution_modes') != ['host']:
        subprocess.run(['docker','info'],check=True,capture_output=True,timeout=30)
    acc=account(int(credential.get('business_uid',29000)),int(credential.get('business_gid',29000)))
    if 'host' in credential.get('execution_modes',['docker','host']):verify_system(acc)
    kind=credential.get('accelerator_type','gpu')
    if kind=='npu':
        available=discover_npu();indices=available if selection=='all' else [int(x) for x in selection.split(',')]
        if not indices or not set(indices)<=set(available):raise ValueError('selected NPU does not exist')
    else:indices=discover(credential,selection)
    if len(indices)!=len(set(indices)):raise ValueError('duplicate device index')
    config={**credential,'host_execution':{'approved':True,'user':acc.pw_name,'uid':acc.pw_uid,'gid':acc.pw_gid},'workspace_root':str(WORKSPACES)}
    if kind=='npu':
        config['npu_runtime']={'approved':True,'allow_shared':True,'host':{'approved':True,'user':acc.pw_name,'environment':{}}}
    planned=plan(config,instance,selection,indices,CONTROL)
    for name,_,value in planned:
        if (UNIT_DIR/('mini-eval-worker-managed-'+name+'.service')).exists():raise ValueError('managed Worker unit already exists')
        if kind=='npu' and 'host' in credential.get('execution_modes',['docker','host']):
            from worker.npu_host import probe
            probe(value)
    for path in (CONTROL,CONTROL/'.local',CONTROL/'.local/share',WORKSPACES):
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
        workspace=Path(value['workspace_root']);workspace.mkdir(mode=0o711,parents=True,exist_ok=True)
        for dirname in ('assets','cache','runs'):
            d=workspace/dirname;d.mkdir(mode=0o700,exist_ok=True);os.chown(d,acc.pw_uid,acc.pw_gid)
        check_paths(acc,[('platform code',str(app),os.R_OK|os.X_OK),('workspace',str(workspace),os.X_OK)])
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
