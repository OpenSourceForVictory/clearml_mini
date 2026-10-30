"""Install independent per-GPU workers; no registration or writes during --check."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error


def discover(config, selection):
    if config.get('mock'):
        if selection == 'all':
            raise ValueError('--gpus all requires physical GPUs; specify mock indices explicitly')
        available = None
    else:
        result = subprocess.run(['nvidia-smi', '--query-gpu=index', '--format=csv,noheader,nounits'],
                                check=True, capture_output=True, text=True, timeout=15)
        available = {int(x.strip()) for x in result.stdout.splitlines() if x.strip()}
    indices = sorted(available) if selection == 'all' else [
        int(x) for x in (selection or str(config.get('gpu_index', 0))).split(',')]
    if not indices or len(set(indices)) != len(indices) or any(x < 0 or x > 1023 for x in indices):
        raise ValueError('select unique GPU indices between 0 and 1023')
    if available is not None and not set(indices) <= available:
        raise ValueError('selected GPU index is not present on this host')
    return indices


def identity(config):
    request = urllib.request.Request(config['api_url'].rstrip('/')+'/api/v1/worker/self',
        headers={'Authorization': 'Bearer '+config['token']})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.load(response)
    except urllib.error.HTTPError as e:
        raise ValueError(f'Worker API authentication/check failed (HTTP {e.code}); verify API address and credential') from None
    except (OSError, ValueError):
        raise ValueError('Worker API is unreachable or returned invalid data; verify the address from this host') from None
    if value.get('node_name') != config['node_name']:
        raise ValueError('credential belongs to a different node_name')
    if 'poolless_registration' not in value.get('capabilities', []) and not config.get('pool_id'):
        raise ValueError('upgrade API before installing a poolless Worker')


def plan(config, instance, selection, indices, home, update=False):
    result = []
    for index in indices:
        name = instance if selection is None else f'{instance}-gpu{index}'
        target = home/'.config/mini-eval'/(name+'.json')
        value = dict(config, gpu_index=index)
        value.setdefault('workspace_root', str(home/'.local/share/mini-eval-workspaces'/name))
        # A supplied single-card workspace becomes a distinct per-card workspace.
        if selection is not None and config.get('workspace_root'):
            value['workspace_root'] = str(Path(config['workspace_root'])/name)
        value['state_path'] = str(home/'.local/state/mini-eval'/(name+'.json'))
        if target.exists():
            if not update:
                raise ValueError(f'instance {name} already exists; use --update only after stopping idle instances')
            previous = json.loads(target.read_text())
            value['state_path'] = previous['state_path']
            if previous.get('node_name') != config['node_name'] or previous.get('gpu_index', 0) != index:
                raise ValueError('update cannot replace an existing device identity')
        result.append((name, target, value))
    if len({v['state_path'] for _, _, v in result}) != len(result):
        raise ValueError('each GPU must have an independent recovery state path')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--instance', default='gpu0')
    parser.add_argument('--gpus', help='all or comma-separated device indices; creates INSTANCE-gpuN services')
    parser.add_argument('--update', action='store_true', help='explicitly update stopped, idle instances; preserve recovery paths')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--check', action='store_true', help='read-only connectivity, identity and runtime checks')
    args = parser.parse_args()
    locks = []
    try:
        if not re.fullmatch('[a-zA-Z0-9_.-]+', args.instance):
            raise ValueError('invalid instance name')
        config = json.loads(args.config.read_text())
        for key in ('api_url', 'token', 'node_name', 'runner_image'):
            if not config.get(key):
                raise ValueError('config is missing '+key)
        identity(config)
        subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL, timeout=30)
        for image in {config['runner_image'], *config.get('client_images', [])}:
            subprocess.run(['docker', 'image', 'inspect', image], check=True, stdout=subprocess.DEVNULL, timeout=30)
        indices = discover(config, args.gpus)
        home = Path.home()
        planned = plan(config, args.instance, args.gpus, indices, home, args.update)
        for name, target, value in planned:
            print(f'{name}: GPU {value["gpu_index"]}, config {target}, state {value["state_path"]}')
        if args.check:
            print('API identity and Worker prerequisites verified; nothing registered or installed.')
            return
        # All instances share code: refuse any live worker or unrecovered execution.
        conf = home/'.config/mini-eval'
        conf.mkdir(parents=True, exist_ok=True)
        install_lock = open(conf/'.install.lock', 'a')
        fcntl.flock(install_lock, fcntl.LOCK_EX)
        locks.append(install_lock)
        planned = plan(config, args.instance, args.gpus, indices, home, args.update)
        for existing in conf.glob('*.json'):
            previous = json.loads(existing.read_text())
            state = Path(previous['state_path'])
            state.parent.mkdir(parents=True, exist_ok=True)
            handle = open(str(state)+'.lock', 'a')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                raise ValueError('stop all installed idle Worker instances before updating shared code') from None
            locks.append(handle)
            if state.exists() and json.loads(state.read_text()):
                raise ValueError('recover and finish all existing executions before updating shared Worker code')
            journal = Path(str(state)+'.runs')
            if journal.exists() and any(json.loads(p.read_text()) for p in journal.glob('*.json')):
                raise ValueError('execution journals remain; complete recovery before installation')

        app = home/'.local/share/mini-eval-worker'
        units = home/'.config/systemd/user'
        for directory in (app, conf, home/'.local/state/mini-eval', units):
            directory.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).resolve().parents[1]
        for package in ('worker', 'runner'):
            shutil.copytree(source/package, app/package, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name, target, value in planned:
            flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if args.update else os.O_EXCL)
            with os.fdopen(os.open(target, flags, 0o600), 'w') as f:
                json.dump(value, f, indent=2)
            os.chmod(target, 0o600)
        quote = lambda p: json.dumps(str(p).replace('%', '%%'))
        unit = '[Unit]\nDescription=Mini Eval Worker (%i)\nAfter=network-online.target\n\n[Service]\nType=simple\n'
        config_argument = json.dumps(str(conf).replace('%', '%%')+'/%i.json')
        unit += f'ExecStart={quote(sys.executable)} -m worker.client --config {config_argument}\nWorkingDirectory={quote(app)}\n'
        unit += 'Restart=always\nRestartSec=5\nTimeoutStopSec=20\n\n[Install]\nWantedBy=default.target\n'
        (units/'mini-eval-worker@.service').write_text(unit)
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        parser.exit(1, f'Installation stopped: {e}\n')
    finally:
        for handle in locks:
            handle.close()
    if args.start:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        for name, _, _ in planned:
            subprocess.run(['systemctl', '--user', 'enable', '--now', f'mini-eval-worker@{name}'], check=True)
    else:
        for name, target, _ in planned:
            print(f'Installed {name}. From {app}, run: {sys.executable} -m worker.client --config {target}')


if __name__ == '__main__':
    main()
