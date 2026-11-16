"""Control-plane release helpers; preserve LAN listeners and validate restored backups."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from scripts.fleet_release import ROOT, atomic_json


def compose_command(env_file, image_file):
    return ['docker', 'compose', '--env-file', str(Path(env_file).resolve()), '-p', 'mini-eval',
            '-f', str(ROOT/'deploy/compose.yml'), '-f', str(ROOT/'deploy/lan-compose.yml'),
            '-f', str(Path(image_file).resolve())]


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def checked_run(argv, **kwargs):
    try:
        return subprocess.run(argv, check=True, capture_output=True, timeout=300, **kwargs)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('release command failed; retained state requires investigation') from None


def backup_database(path, run=subprocess.run):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            # Stream, rather than buffering potentially large database contents.
            result = run(['docker', 'exec', 'mini-eval-db-1', 'pg_dump', '-U', 'eval', '-d', 'eval', '-Fc'],
                                    stdout=output, stderr=subprocess.PIPE, timeout=300)
            if result.returncode: raise RuntimeError('database backup failed')
            output.flush(); os.fsync(output.fileno())
        if path.stat().st_size == 0: raise RuntimeError('empty backup')
        digest = digest_file(path)
        atomic_json(path.with_suffix('.manifest.json'), {'sha256': digest, 'bytes': path.stat().st_size})
        return digest
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def verify_backup(path, digest):
    if digest_file(path) != digest: raise ValueError('backup checksum mismatch')


def restore_probe(path, digest, image, run=checked_run):
    """Restore and migrate in a network-isolated temporary database, never overwrite production."""
    verify_backup(path, digest)
    container = None
    try:
        container = run(['docker', 'run', '-d', '--rm', '--network', 'none',
                         '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_USER=eval',
                         '-e', 'POSTGRES_DB=eval', 'm.daocloud.io/docker.io/library/postgres:16.8']).stdout.decode().strip()
        if not container or len(container) != 64 or any(c not in '0123456789abcdef' for c in container):
            raise RuntimeError('invalid recovery container identity')
        for _ in range(40):
            ready = subprocess.run(['docker', 'exec', container, 'pg_isready', '-U', 'eval'],capture_output=True, timeout=5)
            if ready.returncode == 0: break
            time.sleep(.5)
        else: raise RuntimeError('recovery database did not become ready')
        with Path(path).open('rb') as source:
            run(['docker', 'exec', '-i', container, 'pg_restore', '-U', 'eval', '-d', 'eval',
                 '--no-owner', '--exit-on-error'], stdin=source)
        before = run(['docker', 'exec', container, 'psql', '-U', 'eval', '-d', 'eval', '-Atc',
                      'SELECT version_num FROM alembic_version; SELECT count(*) FROM evaluation_runs;']).stdout.decode().strip()
        run(['docker', 'run', '--rm', '--network', 'container:'+container,
             '-e', 'DATABASE_URL=postgresql+psycopg://eval@127.0.0.1:5432/eval', image,
             'alembic', '-c', 'backend/alembic.ini', 'upgrade', 'head'])
        after = run(['docker', 'exec', container, 'psql', '-U', 'eval', '-d', 'eval', '-Atc',
                     'SELECT version_num FROM alembic_version; SELECT count(*) FROM evaluation_runs;']).stdout.decode().strip()
        if before.splitlines()[1:] != after.splitlines()[1:]: raise RuntimeError('restored migration changed historical execution count')
        return {'before': before.splitlines(), 'after': after.splitlines(), 'backup_sha256': digest}
    finally:
        if container:
            run(['docker', 'stop', container])
