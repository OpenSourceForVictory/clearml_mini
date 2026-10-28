"""Install a standard-library worker and optional systemd user service."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--instance', default='gpu0')
    parser.add_argument('--start', action='store_true', help='enable and start the systemd user service')
    parser.add_argument('--check', action='store_true', help='validate prerequisites without installing')
    args = parser.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_.-]+', args.instance):
        parser.error('instance must contain only letters, digits, dot, underscore or hyphen')
    config = json.loads(args.config.read_text())
    for key in ('api_url', 'token', 'node_name', 'pool_id', 'runner_image'):
        if not config.get(key):
            parser.error('config is missing ' + key)
    subprocess.run(['docker', 'info'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'image', 'inspect', config['runner_image']], check=True, stdout=subprocess.DEVNULL)
    if not config.get('mock'):
        subprocess.run(['nvidia-smi', '--id='+str(config.get('gpu_index', 0))], check=True, stdout=subprocess.DEVNULL)
    if args.check:
        print('Worker prerequisites are available.')
        return
    home = Path.home()
    app = home / '.local/share/mini-eval-worker'
    conf = home / '.config/mini-eval'
    state = home / '.local/state/mini-eval'
    units = home / '.config/systemd/user'
    for directory in (app, conf, state, units):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'worker/client.py', app / 'worker.py')
    config['state_path'] = str(state / (args.instance+'.json'))
    target = conf / (args.instance+'.json')
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(config, f, indent=2)
    os.chmod(target, 0o600)
    quote = lambda p: json.dumps(str(p).replace('%', '%%'))
    unit = '[Unit]\nDescription=Mini Eval Worker (%i)\nAfter=network-online.target\n\n[Service]\nType=simple\n'
    config_argument = json.dumps(str(conf).replace('%', '%%')+'/%i.json')
    unit += f'ExecStart={quote(sys.executable)} {quote(app / "worker.py")} --config {config_argument}\n'
    unit += 'Restart=always\nRestartSec=5\nTimeoutStopSec=20\n\n[Install]\nWantedBy=default.target\n'
    (units / 'mini-eval-worker@.service').write_text(unit)
    print(f'Installed config: {target}')
    if args.start:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', f'mini-eval-worker@{args.instance}'], check=True)
    else:
        print(f'Run: {sys.executable} {app / "worker.py"} --config {target}')
        print(f'Or enable systemd: systemctl --user daemon-reload && systemctl --user enable --now mini-eval-worker@{args.instance}')


if __name__ == '__main__':
    main()
