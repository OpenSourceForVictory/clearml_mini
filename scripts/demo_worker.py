"""Run a CPU-only local demo worker against this project's control node."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    config_path = ROOT/'.runtime/local-mock.json'
    if not config_path.exists():
        values = dict(line.split('=', 1) for line in args.env_file.read_text().splitlines() if '=' in line and not line.startswith('#'))
        base = 'http://127.0.0.1:'+values.get('API_BIND_PORT', '8000')
        def request(path, data=None, token=None):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer '+token
            req = urllib.request.Request(base+'/api/v1/'+path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.load(response)
        token = request('auth/login', {'username': values['ADMIN_USERNAME'], 'password': values['ADMIN_PASSWORD']})['access_token']
        pools = request('pools', token=token)
        pool = next(p for p in pools if p['name'] == 'mock')
        node = request('admin/worker-credentials', {'node_name': 'local-mock'}, token)
        config = {'api_url': base, 'token': node['token'], 'node_name': node['node_name'], 'pool_id': pool['id'],
                  'gpu_index': 0, 'mock': True, 'runner_image': values.get('RUNNER_IMAGE', 'mini-eval-runner:dev')}
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(config_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as f:
            json.dump(config, f, indent=2)
    if args.prepare_only:
        print(f'Local mock configuration prepared: {config_path}')
        return
    command = [sys.executable, '-m', 'worker.client', '--config', str(config_path)]
    if args.once:
        command.append('--once')
    os.chdir(ROOT)
    os.execv(sys.executable, command)


if __name__ == '__main__':
    main()
