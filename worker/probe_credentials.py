"""Probe-only credentials: reference names may travel; values never do."""
import os
from pathlib import Path
import re


def available(config):
    approved=config.get('probe_environment',[])
    if not isinstance(approved,list):return []
    return sorted({name for name in approved if isinstance(name,str) and re.fullmatch(r'[A-Z_][A-Z0-9_]{0,99}',name) and os.environ.get(name)})


def resolve(probe, config):
    allowed=set(available(config))
    refs=set(probe.get('headers_from_env',{}).values())
    if probe.get('ca_env'):refs.add(probe['ca_env'])
    if refs-allowed:raise ValueError('probe credential reference is missing or not approved')
    headers=dict(probe.get('headers',{}))
    for key,ref in probe.get('headers_from_env',{}).items():
        value=os.environ[ref]
        if len(value)>4096 or any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('invalid probe credential')
        headers[key]=value
    ca=None
    if probe.get('ca_env'):
        ca=os.environ[probe['ca_env']]
        if not Path(ca).is_absolute() or not Path(ca).is_file():raise ValueError('probe CA reference is unavailable')
    return headers,ca
