"""Persistent artifact paths, independent of disposable execution workspaces."""
import os
from pathlib import Path
import pwd
import subprocess
import sys
import uuid

from .workspace import checked_path, contains

LAYOUT = 'batch-v1'


def validate_root(config):
    value = config.get('artifact_root')
    if not value:
        return None
    root = checked_path(value)
    for key in ('workspace_root', 'assets_root', 'cache_root'):
        other = checked_path(config[key])
        if contains(root, other) or contains(other, root):
            raise ValueError('artifact_root must be separate from workspace, assets and cache')
    return root


def location(config, task):
    if task.get('artifact_layout') != LAYOUT:
        return None
    root = validate_root(config)
    if task.get('artifact_root') and str(root)!=task['artifact_root']:
        raise ValueError('artifact root differs from frozen execution plan')
    if root is None:
        raise ValueError('independent artifact root is not configured')
    batch = str(uuid.UUID(task['batch_id'])) if task.get('batch_id') else 'standalone'
    return root/batch/str(uuid.UUID(task['job_id']))/str(uuid.UUID(task['execution_id']))


def output(config, task, directory):
    root = location(config, task)
    return root/'output' if root else directory/'output'


def create_tree(path, *, container=False):
    # Every mkdir is anchored to an opened no-follow directory, including existing
    # ancestors. Never chmod/chown an existing shared root or follow a link.
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
    try:
        for name in Path(path).parts[1:]:
            created=False
            try:os.mkdir(name, mode=0o755, dir_fd=fd);created=True
            except FileExistsError:pass
            child = os.open(name, (os.O_RDONLY if created else os.O_PATH) | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            if created:os.fchmod(child,0o755)
            os.close(fd);fd=child
        for name in ('output', 'episodes'):
            created=False
            try:os.mkdir(name, mode=0o777 if container else 0o755, dir_fd=fd);created=True
            except FileExistsError:pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if container or created:os.fchmod(child,0o777 if container else 0o755)
            finally:os.close(child)
    finally:os.close(fd)


def prepare(config, task, account=None, *, container=False):
    root = location(config, task)
    if root is None:
        return None
    if account is None and config.get('host_execution', {}).get('approved'):
        from .host_identity import settings
        account = settings(config) if os.geteuid()==0 else pwd.getpwuid(os.geteuid())
    if account and account.pw_uid != os.geteuid():
        if os.geteuid()!=0:raise RuntimeError('cannot create artifact directory as business account')
        program = 'import sys;sys.path.insert(0,sys.argv[1]);from worker.artifact_layout import create_tree;create_tree(sys.argv[2],container=sys.argv[3]=="1")'
        result = subprocess.run([sys.executable,'-I','-c',program,str(Path(__file__).resolve().parent.parent),
            str(root),'1' if container else '0'],user=account.pw_uid,group=account.pw_gid,
            extra_groups=os.getgrouplist(account.pw_name,account.pw_gid),
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},cwd='/',capture_output=True,timeout=20)
        if result.returncode:
            raise RuntimeError('business account cannot create artifact directory: '+result.stderr.decode(errors='replace')[-500:])
    else:
        create_tree(root,container=container)
    return root


def runner_config(root):
    return {'_artifact_layout':LAYOUT,'_artifact_root':str(root)} if root else {}
