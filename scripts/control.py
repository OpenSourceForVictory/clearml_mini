"""One-command control node deployment; only manages the mini-eval Compose project."""
import argparse
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def compose():
    custom = os.getenv('MINIEVAL_COMPOSE')
    if custom:
        return [custom]
    if subprocess.run(['docker', 'compose', 'version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return ['docker', 'compose']
    if shutil.which('docker-compose'):
        return ['docker-compose']
    raise SystemExit('Install Docker Compose or set MINIEVAL_COMPOSE to its executable path. No Docker restart is required.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['up', 'down', 'status', 'backup'])
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--port', type=int, help='set the API host port (default 8000 on first creation)')
    parser.add_argument('--output', type=Path, help='backup destination (must not exist)')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    env = args.env_file.resolve()
    if not env.exists():
        if args.action != 'up':
            raise SystemExit('Environment file does not exist; run up first.')
        env.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as f:
            f.write('POSTGRES_PASSWORD='+secrets.token_urlsafe(24)+'\nJWT_SECRET='+secrets.token_urlsafe(48)+
                    '\nADMIN_USERNAME=admin\nADMIN_PASSWORD='+secrets.token_urlsafe(24)+f'\nAPI_BIND_PORT={args.port or 8000}\n')
        print(f'Initial admin credentials saved to {env} (mode 0600).')
    elif args.port is not None and args.action == 'up':
        lines = [line for line in env.read_text().splitlines() if not line.startswith('API_BIND_PORT=')]
        env.write_text('\n'.join(lines)+f'\nAPI_BIND_PORT={args.port}\n')
    command = compose() + ['--env-file', str(env), '-p', 'mini-eval', '-f', str(ROOT / 'deploy/compose.yml')]
    if args.action == 'up':
        subprocess.run(command + ['build', 'api'], check=True, cwd=ROOT)
        subprocess.run(command + ['up', '-d', '--wait'], check=True, cwd=ROOT)
        print(f'Control node ready. Open the API_BIND_PORT configured in {env}.')
    elif args.action == 'down':
        subprocess.run(command + ['down'], check=True, cwd=ROOT)  # Persistent volumes are preserved.
    elif args.action == 'status':
        subprocess.run(command + ['ps', '-a'], check=True, cwd=ROOT)
    else:
        if args.output is None:
            parser.error('backup requires --output')
        target = args.output.resolve()
        with os.fdopen(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
            result = subprocess.run(command + ['exec', '-T', 'db', 'pg_dump', '-U', 'eval', '-d', 'eval', '-Fc'], stdout=output)
        if result.returncode:
            target.unlink()
            raise SystemExit('Backup failed; incomplete file removed.')
        digest = hashlib.sha256()
        with target.open('rb') as data:
            for chunk in iter(lambda: data.read(1024*1024), b''):
                digest.update(chunk)
        target.with_suffix(target.suffix+'.sha256').write_text(digest.hexdigest()+'  '+str(target)+'\n')
        print(f'Backup saved to {target}')


if __name__ == '__main__':
    main()
