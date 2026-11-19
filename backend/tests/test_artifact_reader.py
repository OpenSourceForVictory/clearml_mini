import base64
import errno
import json
import os
import pwd
import subprocess
import sys
from types import SimpleNamespace

import pytest

from worker import artifact_access as access
from worker import artifact_reader as reader


@pytest.fixture
def root(tmp_path):
    folder = tmp_path/'output'
    folder.mkdir()
    return folder, [folder.stat().st_dev, folder.stat().st_ino]


def ask(root, **request):
    return reader.respond(str(root[0]), root[1], request)


def test_list_stat_ranges_and_empty(root):
    folder, _ = root
    (folder/'中文 file.json').write_bytes(b'0123456789')
    (folder/'nested').mkdir()
    (folder/'empty').touch()
    (folder/'.mini-eval-protocol').mkdir()
    first = ask(root, operation='list', limit=1)
    assert first['status'] == 'ready' and len(first['items']) == 1 and first['next']
    second = ask(root, operation='list', limit=2, after=first['next'], version=first['version'])
    assert {first['items'][0]['name'], *(i['name'] for i in second['items'])} == {'中文 file.json', 'nested', 'empty'}
    assert second['next'] is None
    info = ask(root, operation='stat', path='中文 file.json')
    block = ask(root, operation='read', path='中文 file.json', version=info['version'], offset=3, length=4)
    assert base64.b64decode(block['data']) == b'3456'
    assert ask(root, operation='read', path='中文 file.json', version=info['version'], offset=10)['data'] == ''
    empty = ask(root, operation='stat', path='empty')
    assert ask(root, operation='read', path='empty', version=empty['version'])['data'] == ''
    assert ask(root, operation='list', path='nested')['items'] == []


@pytest.mark.parametrize('path', ['/etc/passwd', '../x', 'a/../b', './x', 'a//b', 'a/', 'a\\b',
    '.mini-eval-protocol/journal', '.mini-eval-artifacts', '\0bad', 'a\nb', 'a\x7fb', 'x'*4097, '\udcff', 3])
def test_invalid_paths(root, path):
    assert ask(root, operation='stat', path=path)['status'] == 'invalid_path'


@pytest.mark.parametrize('query', [None, [], {'unknown': 1}, {'operation': 'delete'},
    {'operation': 'stat'}, {'operation': 'list', 'limit': 101}, {'operation': 'list', 'limit': True},
    {'operation': 'list', 'limit': 0}, {'operation': 'list', 'after': 0},
    {'operation': 'list', 'after': 'a/b'}, {'operation': 'list', 'after': 'a'}])
def test_invalid_requests(root, query):
    assert reader.respond(str(root[0]), root[1], query)['status'] == 'invalid_request'


@pytest.mark.parametrize('path', ['/', 'relative', '//tmp', '/tmp/../x', '/tmp//a'])
def test_invalid_root(root, path):
    with pytest.raises(reader.ReadError):
        reader.parts(path, absolute=True)


def test_changed_missing_and_unsafe(root, tmp_path):
    folder, identity = root
    (folder/'file').write_text('initial')
    info = ask(root, operation='stat', path='file')
    (folder/'file').write_text('changed content')
    assert ask(root, operation='read', path='file', version=info['version'])['status'] == 'file_changed'
    assert ask(root, operation='stat', path='gone')['status'] == 'missing'
    (folder/'link').symlink_to(tmp_path)
    assert ask(root, operation='list', path='link')['status'] == 'unsafe_path'
    (folder/'leaf').symlink_to(folder/'file')
    os.link(folder/'file', folder/'hard')
    os.mkfifo(folder/'pipe')
    for name in ('leaf', 'hard', 'pipe', 'link'):
        assert ask(root, operation='stat', path=name)['status'] == 'unsupported_file'
    assert all(i['kind'] == 'unsupported' for i in ask(root, operation='list')['items'])
    folder.rename(tmp_path/'old')
    folder.mkdir()
    assert reader.respond(str(folder), identity, {'operation': 'list'})['status'] == 'root_changed'


def test_bounds_and_file_change_during_read(root, monkeypatch):
    (root[0]/'file').write_bytes(b'a'*(reader.BLOCK_BYTES+100))
    info = ask(root, operation='stat', path='file')
    assert len(base64.b64decode(ask(root, operation='read', path='file', version=info['version'])['data'])) == reader.BLOCK_BYTES
    assert ask(root, operation='read', path='file')['status'] == 'invalid_request'
    for change in ({'length': 0}, {'length': reader.BLOCK_BYTES+1}, {'offset': -1}, {'offset': 2**63}):
        assert ask(root, operation='read', path='file', version=info['version'], **change)['status'] == 'invalid_request'
    original = os.pread
    def changing(fd, count, offset):
        (root[0]/'file').write_bytes(b'changed')
        return original(fd, count, offset)
    monkeypatch.setattr(os, 'pread', changing)
    assert ask(root, operation='read', path='file', version=info['version'])['status'] == 'file_changed'


def test_directory_changes_and_os_errors(root, monkeypatch):
    info = ask(root, operation='list')
    (root[0]/'file').touch()
    assert ask(root, operation='list', version=info['version'])['status'] == 'file_changed'
    original = os.stat
    def changing(name, **kwargs):
        (root[0]/'new').touch()
        return original(name, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(os, 'stat', changing)
        assert ask(root, operation='list')['status'] == 'file_changed'
    for number, expected in [(errno.EACCES,'permission_denied'), (errno.EPERM,'permission_denied'),
                             (errno.ELOOP,'unsafe_path'), (errno.EIO,'io_error')]:
        with monkeypatch.context() as m:
            def fail(*args, **kwargs):raise OSError(number, 'private path must not be leaked')
            m.setattr(os, 'open', fail)
            assert ask(root, operation='list') == {'status': expected}


def test_pinned_file_does_not_follow_replacement(root, monkeypatch):
    (root[0]/'file').write_text('safe')
    original = os.open
    def replace(path, flags, *args, **kwargs):
        if str(path).startswith('/proc/self/fd/'):
            (root[0]/'file').unlink()
            (root[0]/'file').symlink_to('/etc/passwd')
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', replace)
    result = ask(root, operation='stat', path='file')
    assert result['size_bytes'] == 4


def registration(root):
    return access.register(root[0], [root[0].parent], pwd.getpwuid(os.geteuid()), 3)


def test_registration_and_real_subprocess(root):
    item = registration(root)
    (root[0]/'file').write_text('actual subprocess')
    result = access.read(item, {'operation':'stat', 'path':'file'}, approval_revision=3)
    assert result['status'] == 'ready'
    result = access.read(item, {'operation':'read', 'path':'file', 'version':result['version']}, approval_revision=3)
    assert base64.b64decode(result['data']) == b'actual subprocess'
    with pytest.raises(ValueError, match='outside'):
        access.register(root[0], ['/elsewhere'], pwd.getpwuid(os.geteuid()), 3)
    with pytest.raises(ValueError, match='revision'):
        access.register(root[0], [root[0]], pwd.getpwuid(os.geteuid()), True)
    assert access.read(item, {}, approval_revision=4) == {'status':'approval_changed'}


def test_account_changes_and_wrong_effective_identity(root, monkeypatch):
    item = registration(root)
    with monkeypatch.context() as m:
        def missing(name):raise KeyError(name)
        m.setattr(pwd, 'getpwnam', missing)
        assert access.read(item, {}, approval_revision=3)['status'] == 'account_missing'
    item['account']['home'] = '/changed'
    assert access.read(item, {}, approval_revision=3)['status'] == 'account_changed'
    item = registration(root)
    monkeypatch.setattr(os, 'geteuid', lambda: 123456)
    assert access.read(item, {}, approval_revision=3)['status'] == 'permission_denied'


@pytest.mark.parametrize('mode', ['timeout', 'oserror', 'exit', 'json', 'list', 'status'])
def test_reader_failures(root, monkeypatch, mode):
    item = registration(root)
    def run(*args, **kwargs):
        if mode == 'timeout':
            raise subprocess.TimeoutExpired('reader', 10)
        if mode == 'oserror':
            raise OSError()
        return SimpleNamespace(returncode=1 if mode=='exit' else 0,
            stdout={'json':'broken', 'list':'[]', 'status':'{"status": 1}'}.get(mode, '{}'))
    monkeypatch.setattr(subprocess, 'run', run)
    assert access.read(item, {}, approval_revision=3)['status'] == ('timeout' if mode=='timeout' else 'reader_unavailable')
    assert access.read(item, {'path':'a'*20000}, approval_revision=3)['status'] == 'invalid_request'


def test_root_demotes_with_clean_environment(root, monkeypatch):
    item = registration(root)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    def run(command, **kwargs):
        assert command[1] == '-I'
        assert kwargs['user'] == item['account']['uid']
        assert kwargs['extra_groups'] == item['account']['groups']
        assert kwargs['env'] == {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}
        assert kwargs['cwd'] == '/' and kwargs['close_fds']
        return SimpleNamespace(returncode=0, stdout='{"status":"ready"}')
    monkeypatch.setattr(subprocess, 'run', run)
    assert access.read(item, {}, approval_revision=3)['status'] == 'ready'


def test_helper_main(root, monkeypatch, capsys):
    import io
    body = json.dumps({'root':str(root[0]), 'root_identity':root[1], 'request':{'operation':'list'}}).encode()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(body)))
    reader.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'ready'


@pytest.mark.skipif(os.geteuid() == 0, reason='must exercise actual non-root filesystem permissions')
def test_real_business_permissions_and_search_only_ancestors(root):
    item = registration(root)
    parent = root[0].parent
    original = parent.stat().st_mode & 0o777
    try:
        parent.chmod(0o111)
        (root[0]/'file').write_text('business-readable')
        result = access.read(item, {'operation':'stat', 'path':'file'}, approval_revision=3)
        assert result['status'] == 'ready'
        (root[0]/'file').chmod(0)
        assert access.read(item, {'operation':'stat', 'path':'file'}, approval_revision=3) == {'status':'permission_denied'}
        (root[0]/'file').chmod(0o600)
        root[0].chmod(0o111)
        assert access.read(item, {'operation':'list'}, approval_revision=3) == {'status':'permission_denied'}
        assert access.read(item, {'operation':'stat', 'path':'file'}, approval_revision=3)['status'] == 'ready'
    finally:
        parent.chmod(original)
        root[0].chmod(0o700)


def test_nested_paths_root_symlink_and_request_limits(root, tmp_path):
    (root[0]/'a').mkdir()
    (root[0]/'a'/'file').write_text('nested')
    assert ask(root, operation='stat', path='a/file')['size_bytes'] == 6
    (tmp_path/'link').symlink_to(root[0], target_is_directory=True)
    with pytest.raises(OSError):
        access.register(tmp_path/'link', [tmp_path], pwd.getpwuid(os.geteuid()), 0)
    for value in ('bad\x7f', 'bad\n', '/absolute', '..'):
        assert ask(root, operation='list', after=value)['status'] == 'invalid_path'
    assert ask(root, operation='read', path='a/file', version=ask(root,operation='stat',path='a/file')['version'], length=True)['status'] == 'invalid_request'


def test_nonroot_gid_and_group_mismatch(root, monkeypatch):
    item = registration(root)
    monkeypatch.setattr(os, 'geteuid', lambda: item['account']['uid'] or 10000)
    with monkeypatch.context() as m:
        m.setattr(os, 'getegid', lambda: 123456)
        assert access.read(item, {}, approval_revision=3)['status'] == 'permission_denied'
    monkeypatch.setattr(os, 'getgroups', lambda: [123456])
    assert access.read(item, {}, approval_revision=3)['status'] == 'permission_denied'


def test_helper_entrypoint(root, monkeypatch, capsys):
    import io
    import runpy
    body = json.dumps({'root':str(root[0]), 'root_identity':root[1], 'request':{'operation':'list'}}).encode()
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(body)))
    runpy.run_path(reader.__file__, run_name='__main__')
    assert json.loads(capsys.readouterr().out)['status'] == 'ready'


@pytest.mark.skipif(os.environ.get('MINI_EVAL_TEST_ARTIFACT_DOCKER') != '1', reason='opt-in real local Docker artifact test')
def test_real_docker_output_survives_container_removal(tmp_path):
    import hashlib
    import uuid
    from worker.workspace import execution_dirs, prepare
    execution = str(uuid.uuid4())
    output = execution_dirs(prepare({}, tmp_path), execution)/'output'
    name = 'mini-eval-artifact-reader-' + execution
    payload = b'node-only-output\n' * 131072
    try:
        subprocess.run(['docker', 'run', '--rm', '--name', name, '--pull', 'never', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '32', '--memory', '128m', '--cpus', '0.25',
            '--user', f'{os.getuid()}:{os.getgid()}', '--mount', f'type=bind,source={output},target=/output',
            '--entrypoint', 'python', 'mini-eval-runner:dev', '-c',
            "from pathlib import Path;Path('/output/result.bin').write_bytes(b'node-only-output\\n'*131072)"],
            check=True, capture_output=True, text=True, timeout=30)
        gone = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True, timeout=10)
        assert gone.returncode != 0 and 'no such object' in gone.stderr.lower()
        item = access.register(output, [tmp_path], pwd.getpwuid(os.geteuid()), 1)
        info = access.read(item, {'operation':'stat', 'path':'result.bin'}, approval_revision=1)
        assert info['status'] == 'ready' and info['size_bytes'] == len(payload)
        digest = hashlib.sha256()
        for offset in range(0, len(payload), reader.BLOCK_BYTES):
            block = access.read(item, {'operation':'read', 'path':'result.bin',
                'version':info['version'], 'offset':offset}, approval_revision=1)
            assert block['status'] == 'ready'
            digest.update(base64.b64decode(block['data']))
        assert digest.digest() == hashlib.sha256(payload).digest()
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)
