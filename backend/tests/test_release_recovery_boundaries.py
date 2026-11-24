"""Release recovery fault injection: no Docker commands or production access."""
import json
import stat
import subprocess
from types import SimpleNamespace

import pytest
from scripts import fleet_release_control as control


def test_backup_stream_manifest_permissions_and_empty_rejection(tmp_path):
    path = tmp_path / 'private' / 'backup.dump'
    def dump(argv, **kwargs):
        assert argv[-1] == '-Fc' and kwargs['timeout'] == 300
        kwargs['stdout'].write(b'validated archive')
        return SimpleNamespace(returncode=0)
    digest = control.backup_database(path, run=dump)
    assert path.read_bytes() == b'validated archive'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.with_suffix('.manifest.json').read_text()) == {
        'sha256': digest, 'bytes': len(b'validated archive')}
    control.verify_backup(path, digest)
    empty = tmp_path / 'empty.dump'
    with pytest.raises(RuntimeError, match='empty backup'):
        control.backup_database(empty, run=lambda *a, **kw: SimpleNamespace(returncode=0))
    assert not empty.exists() and not empty.with_suffix('.manifest.json').exists()


@pytest.mark.parametrize('failure', [OSError('sensitive command'),
    subprocess.TimeoutExpired(['private-argument'], 300),
    subprocess.CalledProcessError(1, ['private-argument'])])
def test_checked_run_redacts_command_failures(monkeypatch, failure):
    def run(*args, **kwargs):
        assert kwargs == {'check': True, 'capture_output': True, 'timeout': 300}
        raise failure
    monkeypatch.setattr(control.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as caught:
        control.checked_run(['private-argument'])
    assert str(caught.value) == 'release command failed; retained state requires investigation'
    assert caught.value.__suppress_context__


def test_checked_run_returns_output(monkeypatch):
    expected = SimpleNamespace(stdout=b'container-id')
    monkeypatch.setattr(control.subprocess, 'run', lambda *a, **kw: expected)
    assert control.checked_run(['isolated-command']) is expected


@pytest.mark.parametrize('mode', ['success', 'not-ready', 'restore-failed', 'count-changed', 'invalid-id'])
def test_restore_probe_isolated_order_and_cleanup(tmp_path, monkeypatch, mode):
    path = tmp_path / 'backup'; path.write_bytes(b'archive')
    digest = control.digest_file(path)
    cid = 'a' * 64
    commands = []; probes = []; sleeps = []
    def ready(argv, **kwargs):
        probes.append(argv)
        assert argv == ['docker', 'exec', cid, 'pg_isready', '-U', 'eval']
        assert kwargs == {'capture_output': True, 'timeout': 5}
        return SimpleNamespace(returncode=1 if mode == 'not-ready' or len(probes) == 1 else 0)
    monkeypatch.setattr(control.subprocess, 'run', ready)
    monkeypatch.setattr(control.time, 'sleep', sleeps.append)
    def run(argv, **kwargs):
        commands.append(argv)
        if argv[1:3] == ['run', '-d']:
            assert argv[argv.index('--network') + 1] == 'none'
            return SimpleNamespace(stdout=(('invalid' if mode == 'invalid-id' else cid) + '\n').encode())
        if 'pg_restore' in argv:
            assert kwargs['stdin'].read() == b'archive'
            assert '--exit-on-error' in argv and '--no-owner' in argv
            if mode == 'restore-failed': raise RuntimeError('restore failed')
        if 'psql' in argv:
            migrated = any('alembic' in x for x in commands)
            return SimpleNamespace(stdout=(b'new\n8\n' if migrated and mode == 'count-changed'
                else b'new\n7\n' if migrated else b'old\n7\n'))
        if 'alembic' in argv:
            assert argv[argv.index('--network') + 1] == 'container:' + cid
            assert argv[-2:] == ['upgrade', 'head']
        return SimpleNamespace(stdout=b'')
    if mode == 'success':
        assert control.restore_probe(path, digest, 'test-image', run=run) == {
            'before': ['old', '7'], 'after': ['new', '7'], 'backup_sha256': digest}
        assert len(probes) == 2 and sleeps == [.5]
    else:
        message = {'not-ready': 'did not become ready', 'restore-failed': 'restore failed',
                   'count-changed': 'changed historical execution count', 'invalid-id': 'invalid recovery container identity'}[mode]
        with pytest.raises(RuntimeError, match=message):
            control.restore_probe(path, digest, 'test-image', run=run)
    assert commands[-1] == ['docker', 'stop', 'invalid' if mode == 'invalid-id' else cid]
    assert path.read_bytes() == b'archive'
    if mode in ('not-ready', 'invalid-id', 'restore-failed'):
        assert not any('alembic' in x for x in commands)
    if mode == 'not-ready': assert len(probes) == 40


@pytest.mark.parametrize('base', ['file:///tmp/api', 'http://', 'http://user:password@localhost'])
def test_release_api_rejects_invalid_platform_urls(base):
    from scripts.fleet_release_api import API
    with pytest.raises(ValueError, match='invalid platform URL'):
        API(base, 'test-only-token')


def test_release_api_http_contract_and_failure_redaction():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from scripts.fleet_release_api import API
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            observed.append((self.path, self.headers.get('Authorization'),
                self.headers.get('Idempotency-Key'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"revision":2}')
        def do_GET(self):
            self.send_response(401 if self.path.endswith('denied') else 200)
            self.end_headers(); self.wfile.write(b'sensitive-response-not-json')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    api = API(f'http://127.0.0.1:{server.server_port}/', 'test-only-token')
    try:
        assert api('operation', {'expected_revision': 1}, 'POST', 'unique-operation') == {'revision': 2}
        assert observed == [('/api/v1/operation', 'Bearer test-only-token', 'unique-operation', {'expected_revision': 1})]
        with pytest.raises(RuntimeError, match='^platform request rejected with HTTP 401$'):
            api('denied')
        with pytest.raises(RuntimeError, match='^platform request unavailable; reconcile before repeating mutations$'):
            api('malformed')
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
    with pytest.raises(RuntimeError, match='^platform request unavailable; reconcile before repeating mutations$'):
        api('offline')


def test_restore_creation_failure_does_not_run_cleanup_or_migration(tmp_path):
    path = tmp_path / 'backup'; path.write_bytes(b'archive')
    calls = []
    def fail(argv, **kwargs):
        calls.append(argv)
        raise RuntimeError('container creation failed')
    with pytest.raises(RuntimeError, match='container creation failed'):
        control.restore_probe(path, control.digest_file(path), 'test-image', run=fail)
    assert len(calls) == 1 and calls[0][1:3] == ['run', '-d']
    assert path.read_bytes() == b'archive'


def test_compose_release_keeps_lan_overlay_and_absolute_paths(tmp_path):
    env = tmp_path / 'environment'; image = tmp_path / 'image.yml'
    command = control.compose_command(env, image)
    assert command[:6] == ['docker', 'compose', '--env-file', str(env.resolve()), '-p', 'mini-eval']
    assert command[6:] == ['-f', str(control.ROOT / 'deploy/compose.yml'),
        '-f', str(control.ROOT / 'deploy/lan-compose.yml'), '-f', str(image.resolve())]
