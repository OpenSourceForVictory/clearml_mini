"""Resolve administrator-approved directories immediately before Docker create."""
import os
import tempfile
from .workspace import validate_storage, checked_path
from runner.mount_contract import mounts


def check_directory(entry,config,state_path):
    host=entry['path']
    validate_storage({**config,'assets_root':host,'cache_root':host},state_path,config.get('_config_path'))
    path=checked_path(host)
    if entry.get('writable',False):
        if not os.access(path,os.W_OK|os.X_OK):raise ValueError('approved directory is not writable')
        with tempfile.TemporaryFile(dir=path):pass
    return str(path)


def resolve(declarations,snapshot,config,state_path,directory):
    result=[]
    for name,item in mounts(declarations).items():
        source=item['source'];readonly=item['read_only']
        if source in ('work','output'):host=str(directory/source)
        elif source=='cache':
            host=config['cache_root']
            check_directory({'path':host},config,state_path)
        else:
            kind,key=source.split(':',1);entry=snapshot.get(kind,{}).get(key)
            if entry is None:
                if item['required']:raise ValueError('missing directory mapping: '+name)
                continue
            if kind=='assets':entry={'path':entry,'writable':False}
            if not readonly and not entry.get('writable',False):raise ValueError('directory write not approved: '+name)
            try:host=check_directory(entry,config,state_path)
            except (ValueError,OSError) as exc:raise ValueError('directory is not ready: '+name) from exc
        result.append((host,item['target'],readonly))
    return result
