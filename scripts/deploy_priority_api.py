"""Upgrade an existing Compose control plane to the four-role API topology."""

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from scripts.control import ROOT, compose


ACTIVE_SQL = ("SELECT count(*) FROM evaluation_runs WHERE "
              "status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED') "
              "OR cleanup_confirmed = false")


def run(command, *, capture=False, stdout=None, input_file=None):
    with input_file.open('rb') if input_file else nullcontext() as source:
        result = subprocess.run(command, cwd=ROOT, check=True, stdout=stdout or (subprocess.PIPE if capture else None),
                                stdin=source, text=capture, stderr=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def revision(output):
    revisions = [match.group(1) for line in output.splitlines()
                 if (match := re.match(r'^([a-zA-Z0-9]+)\b', line.strip()))]
    if len(revisions) != 1:
        raise RuntimeError(f'Expected one Alembic revision, got {len(revisions)}')
    return revisions[0]


def verify_images(config_text):
    services = json.loads(config_text)['services']
    names = ('api', 'migrate', 'bootstrap', 'execution-control-api', 'worker-data-api', 'bulk-transfer-api')
    images = {name: services.get(name, {}).get('image') for name in names}
    if not images['api'] or len(set(images.values())) != 1:
        raise RuntimeError('API, migration, bootstrap and all four roles must use the same target image')


def deploy(args):
    env = args.env_file.resolve()
    if not env.is_file():
        raise RuntimeError(f'Environment file does not exist: {env}')
    if not args.drained:
        raise RuntimeError('Drain tasks and pass --drained before upgrading an existing platform')
    if args.image_mode == 'pull' and not args.overlay:
        raise RuntimeError('Pull mode requires a versioned --overlay setting the same API image for api/migrate/bootstrap')
    old_files = [ROOT / 'deploy/compose.yml', *args.previous_overlay]
    new_files = [ROOT / 'deploy/compose.yml', ROOT / 'deploy/priority-api-compose.yml', *args.overlay]
    if any('split-api-compose.yml' == path.name for path in args.overlay):
        raise RuntimeError('Do not combine the split-API and four-role overlays')
    for path in [*old_files, *new_files]:
        if not path.is_file():
            raise RuntimeError(f'Compose file does not exist: {path}')
    base = compose() + ['--env-file', str(env), '-p', args.project]
    old = base + [item for path in old_files for item in ('-f', str(path.resolve()))]
    new = base + [item for path in new_files for item in ('-f', str(path.resolve()))]
    run(old + ['config', '--quiet'])
    run(new + ['config', '--quiet'])
    verify_images(run(new + ['config', '--format', 'json'], capture=True))
    running = set(run(old + ['ps', '--services', '--filter', 'status=running'], capture=True).splitlines())
    if 'db' not in running:
        raise RuntimeError('Existing database is not running under this project/configuration')
    if args.image_mode == 'build':
        run(new + ['build', 'api', 'migrate', 'bootstrap', 'onboarding'])
    else:
        run(new + ['pull', 'api', 'migrate', 'bootstrap', 'onboarding'])
    target = revision(run(new + ['run', '--rm', '--no-deps', 'migrate',
                                 'alembic', '-c', 'backend/alembic.ini', 'heads'], capture=True))
    active = run(old + ['exec', '-T', 'db', 'psql', '-U', 'eval', '-d', 'eval', '-tAc', ACTIVE_SQL], capture=True)
    if active != '0':
        raise RuntimeError(f'{active} active/unconfirmed executions remain; no API was stopped')
    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_dir / f'{args.project}-before-{target}.dump'
    if backup.exists():
        raise RuntimeError(f'Backup already exists: {backup}; choose another --backup-dir')
    try:
        with os.fdopen(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
            run(old + ['exec', '-T', 'db', 'pg_dump', '-U', 'eval', '-d', 'eval', '-Fc'], stdout=output)
        run(old + ['exec', '-T', 'db', 'pg_restore', '--list'], input_file=backup, capture=True)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    digest_hash = hashlib.sha256()
    with backup.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest_hash.update(chunk)
    digest = digest_hash.hexdigest()
    print(f'Backup verified: {backup} SHA256 {digest}', flush=True)
    stopped = sorted(running.intersection({'api', 'api-gateway', 'control-api', 'execution-control-api',
                                           'worker-data-api', 'bulk-transfer-api'}))
    if stopped:
        run(old + ['stop', *stopped])
    # The four-role database default raises max_connections; this may restart PostgreSQL.
    run(new + ['up', '-d', '--no-deps', '--wait', 'db'])
    maximum = int(run(new + ['exec', '-T', 'db', 'psql', '-U', 'eval', '-d', 'eval',
                             '-tAc', 'SHOW max_connections'], capture=True))
    if maximum < 200:
        raise RuntimeError(f'PostgreSQL max_connections={maximum}; four-role default requires at least 200')
    run(new + ['run', '--rm', '--no-deps', 'migrate'])
    current = revision(run(new + ['run', '--rm', '--no-deps', 'migrate',
                                  'alembic', '-c', 'backend/alembic.ini', 'current'], capture=True))
    if current != target:
        raise RuntimeError(f'Migration mismatch: current={current}, target={target}')
    run(new + ['up', '-d', '--no-build', '--wait', '--remove-orphans'])
    run(new + ['ps'])
    print(f'Four-role deployment ready; migration {current}; backup {backup}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--project', default='mini-eval', help='Existing Compose project name')
    parser.add_argument('--previous-overlay', type=Path, action='append', default=[],
                        help='Repeat in original -f order, after deploy/compose.yml')
    parser.add_argument('--overlay', type=Path, action='append', default=[],
                        help='Repeat for compatible overrides after priority-api-compose.yml')
    parser.add_argument('--image-mode', choices=('build', 'pull'), default='build')
    parser.add_argument('--backup-dir', type=Path, default=ROOT / '.runtime/db-backups')
    parser.add_argument('--drained', action='store_true', help='Confirm no new tasks or onboarding operations will start')
    args = parser.parse_args(argv)
    try:
        deploy(args)
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        parser.exit(1, f'Deployment stopped: {exc}\n')


if __name__ == '__main__':
    main()
