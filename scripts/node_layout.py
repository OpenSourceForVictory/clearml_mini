"""Read-only discovery of root-owned updater layouts. Safe to send over SSH."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

LAYOUT = Path('/opt/mini-eval-updater/config.json')


def trusted(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('existing_layout_untrusted')
    for item in (path, *path.parents):
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('existing_layout_untrusted')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('existing_layout_untrusted')
    return path


def service(name):
    result = subprocess.run(['systemctl', 'show', name, '-p', 'ExecStart', '-p',
                             'WorkingDirectory', '-p', 'User', '-p', 'LoadState'],
                            check=True, capture_output=True, text=True, timeout=15)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if fields.get('LoadState') != 'loaded' or fields.get('User') not in ('', 'root'):
        raise ValueError('existing_layout_services_mismatch')
    command = fields.get('ExecStart', '')
    match = re.fullmatch(r'\{ path=[^;]+ ; argv\[\]=(.*?) ; ignore_errors=.*\}', command)
    if not match:
        raise ValueError('existing_layout_services_mismatch')
    return fields, shlex.split(match[1])


def discover(node_name, python):
    """Return only layout metadata and hashes, never Worker credentials."""
    if os.geteuid() != 0:
        raise ValueError('existing_layout_requires_root')
    if not LAYOUT.exists():
        return None
    raw = trusted(LAYOUT).read_bytes()
    value = json.loads(raw)
    layout = {k: value[k] for k in ('app_dir', 'state_dir', 'config_paths', 'services', 'agent')}
    app = trusted(layout['app_dir'], True)
    trusted(app/'worker/client.py')
    state = trusted(layout['state_dir'], True)
    if state.is_relative_to(app):
        raise ValueError('existing_layout_untrusted')
    if (state/'upgrade.json').exists():
        raise ValueError('upgrade_journal_pending')
    paths = [trusted(p) for p in layout['config_paths']]
    if not paths or len(paths) != len(set(paths)) or any(p.is_relative_to(app) for p in paths):
        raise ValueError('existing_layout_incomplete')
    values = [json.loads(p.read_text()) for p in paths]
    identities = {(v['node_name'], v['api_url'], v['token']) for v in values}
    if len(identities) != 1 or values[0]['node_name'] != node_name:
        raise ValueError('installed_node_identity_conflict')
    indices = [v.get('gpu_index', 0) for v in values]
    if len(set(indices)) != len(indices):
        raise ValueError('existing_layout_incomplete')
    for parent in {p.parent for p in paths}:
        for p in parent.glob('*.json'):
            v = json.loads(trusted(p).read_text())
            if isinstance(v, dict) and 'state_path' in v and p not in paths:
                raise ValueError('existing_layout_incomplete')
    for v in values:
        recovery = Path(v['state_path'])
        if not recovery.is_absolute() or recovery.resolve() != recovery or recovery.is_relative_to(app):
            raise ValueError('existing_layout_untrusted')
        trusted(recovery.parent, True)
        journals = [recovery, *Path(str(recovery)+'.runs').glob('*.json')]
        if any(p.exists() and json.loads(trusted(p).read_text()) for p in journals):
            raise ValueError('execution_journal_pending')
    units = layout['services']
    if len(units) != len(paths) or len({x['name'] for x in units}) != len(units):
        raise ValueError('existing_layout_services_mismatch')
    bindings = []; effective = []
    for unit in units:
        if unit['scope'] != 'system' or not re.fullmatch(r'mini-eval-worker[\w@.\-]*\.service', unit['name']):
            raise ValueError('existing_layout_services_mismatch')
        fields, argv = service(unit['name']); effective.append({'argv':argv,'directory':fields['WorkingDirectory'],'user':fields.get('User','')})
        if fields['WorkingDirectory'] != str(app) or not argv or Path(argv[0]).resolve() != Path(python).resolve():
            raise ValueError('existing_layout_services_mismatch')
        # Only the supported Worker module with an explicit original config.
        if argv[1:] not in (['-m', 'worker.client', '--config', p] for p in map(str, paths)) and argv[1:] not in (['-B', '-m', 'worker.client', '--config', p] for p in map(str, paths)):
            raise ValueError('existing_layout_services_mismatch')
        bindings.append(argv[-1])
    if set(bindings) != set(map(str, paths)):
        raise ValueError('existing_layout_services_mismatch')
    agent = layout['agent']
    if agent['scope'] != 'system' or agent['name'] != 'mini-eval-agent.service' or Path(agent['state_dir']) != state.parent/'mini-eval-agent' or Path(agent['config_path']) != Path(agent['state_dir'])/'config.json':
        raise ValueError('existing_layout_services_mismatch')
    agent_path = trusted(agent['config_path']); agent_value = json.loads(agent_path.read_text())
    if any(agent_value.get(k) != values[0][k] for k in ('api_url', 'node_name', 'token')) or agent_value.get('services') != units:
        raise ValueError('installed_node_identity_conflict')
    for name, expected, cwd in (
        ('mini-eval-agent.service', ['-m', 'node_agent.client', '--config', str(agent_path)], str(app)),
        ('mini-eval-updater.service', [str(LAYOUT.parent/'updater.py'), '--config', str(LAYOUT)], '')):
        fields, argv = service(name); effective.append({'argv':argv,'directory':fields['WorkingDirectory'],'user':fields.get('User','')})
        if not argv or Path(argv[0]).resolve() != Path(python).resolve() or argv[1:] != expected or fields['WorkingDirectory'] != cwd:
            raise ValueError('existing_layout_services_mismatch')
    digest = hashlib.sha256(raw)
    for p in [*paths, agent_path]:
        digest.update(str(p).encode()); digest.update(p.read_bytes())
    digest.update(json.dumps(effective, sort_keys=True).encode())
    return {'layout': layout, 'fingerprint': digest.hexdigest()}
