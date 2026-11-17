"""Standalone root-system cleanup program, transmitted over verified SSH.

Only fixed installation locations are moved. Business workspaces and recovery
state remain in place. No shell, recursive deletion, or user-supplied paths.
"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import shlex
import subprocess
import sys

HOMES=('/var/lib/mini-eval/control','/root')
UNITS='/etc/systemd/system'
BACKUPS='/root/mini-eval-node-backups'
UPDATER='/opt/mini-eval-updater'
LOCK='/run/lock/mini-eval-system-install.lock'


def trusted(path):
    for p in (path,*path.parents):
        info=p.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('cleanup_untrusted_path')
    return path


def systemctl(*args):
    return subprocess.run(['systemctl',*args],check=True,capture_output=True,text=True,timeout=45).stdout


def plan(node_name):
    if os.geteuid()!=0:raise ValueError('cleanup_requires_root')
    paths=[];services=[];hashes={}
    for home in map(Path,HOMES):
        if list((home/'.config/systemd/user').glob('mini-eval-*.service')):
            raise ValueError('cleanup_user_services_unsupported')
        for rel in ('.config/mini-eval','.local/share/mini-eval-worker','.local/state/mini-eval-agent/config.json'):
            p=home/rel
            if p.exists() or p.is_symlink():paths.append(p)
        journals=[*(home/'.local/state/mini-eval').glob('*.json'),*(home/'.local/state/mini-eval').glob('*.runs/*.json'),home/'.local/state/mini-eval-updater/upgrade.json']
        for journal in journals:
            if journal.exists() and json.loads(trusted(journal).read_text()):raise ValueError('cleanup_recovery_pending')
    updater=Path(UPDATER)
    if updater.exists() or updater.is_symlink():paths.append(updater)
    for p in sorted(Path(UNITS).glob('mini-eval-*.service')):
        if not re.fullmatch(r'mini-eval-(agent|updater|worker[\w@.\-]*)\.service',p.name):continue
        trusted(p)
        fields=dict(line.split('=',1) for line in systemctl('show',p.name,'-p','User','-p','FragmentPath','-p','DropInPaths').splitlines() if '=' in line)
        if fields.get('User') not in ('','root') or fields.get('FragmentPath')!=str(p) or fields.get('DropInPaths'):
            raise ValueError('cleanup_service_unsupported')
        # Refuse custom paths; keep those available for controlled manual migration.
        text=p.read_text()
        allowed=[str(Path(h)/rel) for h in HOMES for rel in ('.config/mini-eval','.local/share/mini-eval-worker','.local/state/mini-eval-agent/config.json')]+[UPDATER]
        for line in text.splitlines():
            if line.startswith('Exec') and not line.startswith('ExecStart='):raise ValueError('cleanup_service_unsupported')
            if line.startswith('WorkingDirectory=') and line.split('=',1)[1].strip('"') not in ('',*allowed):
                raise ValueError('cleanup_service_unsupported')
            if line.startswith('ExecStart='):
                args=shlex.split(line.split('=',1)[1])
                if not any(arg==x or arg.startswith(x+'/') for arg in args for x in allowed):raise ValueError('cleanup_service_unsupported')
        paths.append(p);services.append(p.name)
    for line in systemctl('list-units','--all','--type=service','--no-legend','--plain').splitlines():
        name=line.split()[0] if line.split() else ''
        if re.fullmatch(r'mini-eval-(agent|updater|worker[\w@.\-]*)\.service',name) and name not in services:
            raise ValueError('cleanup_service_unsupported')
    for root in paths:
        trusted(root)
        members=[root,*sorted(root.rglob('*'))] if root.is_dir() else [root]
        for p in members:
            trusted(p)
            if os.path.ismount(p):raise ValueError('cleanup_mount_unsupported')
            if not p.is_file() and not p.is_dir():raise ValueError('cleanup_untrusted_path')
            if p.is_file():
                if p.stat().st_size>32*1024*1024:raise ValueError('cleanup_unexpected_large_file')
                data=p.read_bytes();hashes[str(p)]=hashlib.sha256(data).hexdigest()
                if p.suffix=='.json' and ('.config/mini-eval' in str(p) or p.name=='config.json'):
                    value=json.loads(data)
                    if not isinstance(value,dict):raise ValueError('cleanup_invalid_configuration')
                    if value.get('node_name',node_name)!=node_name:raise ValueError('installed_node_identity_conflict')
                    if 'state_path' in value:
                        recovery=Path(value['state_path'])
                        if recovery.resolve()!=recovery or not any(recovery.is_relative_to(Path(h)/'.local/state/mini-eval') for h in HOMES):raise ValueError('cleanup_custom_layout_unsupported')
                    if p==updater/'config.json':
                        permitted={str(Path(h)/'.local/share/mini-eval-worker') for h in HOMES}
                        if any(u.get('scope')!='system' or not re.fullmatch(r'mini-eval-worker[\w@.\-]*\.service',u.get('name','')) for u in value.get('services',[])):raise ValueError('cleanup_custom_layout_unsupported')
                        states={str(Path(h)/'.local/state/mini-eval-updater') for h in HOMES}
                        configs=[Path(x) for x in value.get('config_paths',[])]
                        if value.get('app_dir') not in permitted or value.get('state_dir') not in states or any(x.resolve()!=x or not any(x.is_relative_to(Path(h)/'.config/mini-eval') for h in HOMES) for x in configs):raise ValueError('cleanup_custom_layout_unsupported')
        hashes[str(root)+':metadata']=str(root.stat().st_ino)
    digest=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    return {'paths':[str(p) for p in paths],'services':services,'fingerprint':digest,'backup_root':BACKUPS,'issues':[]}


def execute(node_name,expected,operation):
    with open(LOCK,'a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return execute_locked(node_name,expected,operation)


def execute_locked(node_name,expected,operation):
    if not re.fullmatch('[a-f0-9]{32}',operation):raise ValueError('cleanup_invalid_operation')
    current=plan(node_name)
    if current!=expected:raise ValueError('environment_changed_repeat_check')
    root=Path(BACKUPS);root.mkdir(mode=0o700,exist_ok=True);trusted(root)
    backup=root/operation;backup.mkdir(mode=0o700)
    manifest={'state':'RUNNING','moved':[],'planned':current['paths'],'services':current['services']}
    def persist():
        target=backup/'manifest.json'
        with target.open('w') as f:json.dump(manifest,f);f.flush();os.fsync(f.fileno())
    persist()
    try:
        # Stop supervisors before workers; never restart after partial failure.
        for name in sorted(current['services'],key=lambda s:'worker' in s):systemctl('disable','--now',name)
        for name in current['services']:
            value=systemctl('show',name,'-p','ActiveState','--value').strip()
            if value not in ('inactive','failed'):raise ValueError('cleanup_service_still_active')
        if plan(node_name)!=current:raise ValueError('environment_changed_repeat_check')
        for index,source in enumerate(current['paths']):
            destination=backup/str(index)
            # rename is atomic and refuses cross-filesystem moves; no copy/delete fallback.
            os.rename(source,destination)
            manifest['moved'].append({'source':source,'backup':str(destination)});persist()
        systemctl('daemon-reload')
        manifest['state']='SUCCEEDED';persist()
    except Exception:
        manifest['state']='FAILED';persist()
        return {'state':'FAILED','backup':str(backup),'error':'cleanup_partial_failure'}
    return {'state':'SUCCEEDED','backup':str(backup)}


def main():
    body=json.load(sys.stdin)
    try:
        value=execute(body['node_name'],body['plan'],body['operation']) if body.get('operation') else plan(body['node_name'])
    except (ValueError,TypeError,KeyError,OSError,subprocess.SubprocessError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]+',str(exc)) else 'cleanup_inspection_failed'
        value={'issues':[code]}
    print(json.dumps(value))

if __name__=='__main__':main()
