"""Bounded, read-only inventory and durable release checkpoints for the five-node fleet."""
import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

TARGETS = {
    '192.168.8.151': 'local4090-151',
    '192.168.8.157': 'rtx4090-157',
    '192.168.8.177': 'rtx8000-177',
    '192.168.8.182': 'rtx3080-182',
    '192.168.8.183': 'rtx4090-183',
}
ROOT = Path(__file__).resolve().parents[1]

# This program reads only selected fields. Never emit config files or environment values.
PROBE = r'''
import ast,json,pathlib,subprocess
home=pathlib.Path.home(); app=home/'.local/share/mini-eval-worker'
def command(argv):
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=8)
        return p.stdout.strip() if p.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None
def constants(path,names):
    try:
        result={}
        for n in ast.parse(path.read_text()).body:
            if isinstance(n,ast.Assign):
                for t in n.targets:
                    if isinstance(t,ast.Name) and t.id in names:result[t.id]=ast.literal_eval(n.value)
        return result
    except (OSError,SyntaxError,ValueError):return {}
result={'hostname':command(['hostname']),
        'machine_id':None,
        'addresses':command(['hostname','-I']),
        'worker':constants(app/'worker/version.py',{'SOFTWARE_VERSION','PROTOCOL_VERSION'}),
        'agent':constants(app/'node_agent/__init__.py',{'VERSION'}),
        'mount':command(['findmnt','-rn','--mountpoint','/mnt/eval-share','-o','SOURCE,FSTYPE']),
        'gpus':command(['nvidia-smi','--query-gpu=uuid,name,memory.total','--format=csv,noheader']),
        'services':{}}
try:result['machine_id']=pathlib.Path('/etc/machine-id').read_text().strip()
except OSError:pass
for name in ('mini-eval-worker-nfs','mini-eval-agent','mini-eval-updater'):
    result['services'][name]=command(['systemctl','show',name,'--property=ActiveState,SubState,FragmentPath'])
print(json.dumps(result))
'''


def checked_target(host):
    if host not in TARGETS:
        raise ValueError('target is not in the five-node release allowlist')
    return host


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Checkpoints:
    """A release-scoped lock and intent journal; uncertain steps require reconciliation."""
    def __init__(self, directory, release_id):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', release_id):
            raise ValueError('invalid release ID')
        self.directory = Path(directory)
        self.release_id = release_id
        self.path = self.directory / (release_id + '.json')
        self.state = None

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Fleet-wide, not per release: two different release IDs must not race.
        fd = os.open(self.directory / 'fleet.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = json.loads(self.path.read_text()) if self.path.exists() else {'release_id': self.release_id, 'steps': {}}
            if self.state['release_id'] != self.release_id: raise ValueError('release identity mismatch')
            yield self
        finally:
            self.state = None
            os.close(fd)

    def begin(self, step, inputs):
        if self.state is None: raise RuntimeError('release lock required')
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        previous = self.state['steps'].get(step)
        if previous:
            if previous['input_sha256'] != digest: raise ValueError('step inputs changed')
            if previous['status'] == 'done': return False
            raise RuntimeError('unfinished step requires explicit reconciliation')
        self.state['steps'][step] = {'status': 'started', 'input_sha256': digest}
        atomic_json(self.path, self.state)
        return True

    def finish(self, step):
        if self.state is None: raise RuntimeError('release lock required')
        self.state['steps'][step]['status'] = 'done'
        atomic_json(self.path, self.state)


def probe(host, run=subprocess.run):
    checked_target(host)
    if host == '192.168.8.151':
        argv = ['python3', '-c', PROBE]
    else:
        argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
                '-o', 'ServerAliveCountMax=2', host, shlex.join(['python3', '-c', PROBE])]
    result = {'address': host, 'node_name': TARGETS[host], 'status': 'unknown'}
    try:
        p = run(argv, capture_output=True, text=True, timeout=55)
        if p.returncode: return {**result, 'error': 'probe_failed'}
        facts = json.loads(p.stdout)
        if host not in (facts.get('addresses') or '').split():
            return {**result, 'error': 'address_identity_mismatch'}
        # Explicit allowlist prevents accidental raw remote output in evidence.
        keys = ('hostname', 'machine_id', 'worker', 'agent', 'mount', 'gpus', 'services')
        result.update({k: facts.get(k) for k in keys})
        complete = all(result.get(k) for k in keys)
        services = result.get('services') or {}
        complete = complete and all('ActiveState=active' in (services.get(k) or '') for k in
                                    ('mini-eval-worker-nfs', 'mini-eval-agent', 'mini-eval-updater'))
        result['status'] = 'observed' if complete else 'unknown'
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        result['error'] = 'probe_unavailable'
    return result


def release_plan(inventory):
    """Reject partial/stale identity baselines rather than filling from deployment docs."""
    nodes = inventory.get('nodes', [])
    if len(nodes) != len(TARGETS) or {x['address'] for x in nodes} != set(TARGETS):
        raise ValueError('a release plan requires exactly the five approved nodes')
    if any(x.get('status') != 'observed' or x.get('node_name') != TARGETS[x['address']]
           or not x.get('machine_id') for x in nodes):
        raise ValueError('incomplete node identity')
    if len({x['machine_id'] for x in nodes}) != len(nodes):
        raise ValueError('duplicate physical machine identity')
    from worker.version import SOFTWARE_VERSION, PROTOCOL_VERSION
    from node_agent import VERSION
    return {'targets': [{'address': x['address'], 'node_name': x['node_name'],
                         'machine_id': x['machine_id'], 'gpus': x['gpus'],
                         'mount': x['mount']} for x in nodes],
            'target_worker': SOFTWARE_VERSION, 'target_protocol': PROTOCOL_VERSION,
            'target_agent': VERSION, 'source_observed_at': inventory['observed_at'],
            'requires_fresh_preflight': True}


def verify_identity(expected, observed):
    checked_target(expected['address'])
    if observed.get('status') != 'observed':
        raise ValueError('node is not ready for release')
    for key in ('address', 'node_name', 'machine_id', 'gpus', 'mount'):
        if not expected.get(key) or expected[key] != observed.get(key):
            raise ValueError('node identity or mount changed: ' + key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inventory', 'plan'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--targets', nargs='+', default=list(TARGETS), choices=list(TARGETS))
    args = parser.parse_args()
    if args.action == 'plan':
        if args.inventory is None: parser.error('plan requires --inventory')
        atomic_json(args.output, release_plan(json.loads(args.inventory.read_text())))
        print('Release plan saved; fresh preflight is required before mutations.')
        return 0
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
              'nodes': [probe(host) for host in args.targets]}
    report['complete'] = all(x['status'] == 'observed' for x in report['nodes'])
    atomic_json(args.output, report)
    print(json.dumps({'complete': report['complete'], 'nodes': len(report['nodes']), 'output': str(args.output)}))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
