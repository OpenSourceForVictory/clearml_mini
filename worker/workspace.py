"""Administrator-owned workspace layout. Job payloads never select host paths."""
import os
from pathlib import Path
import uuid


def prepare(config, fallback):
    root = checked_path(config.get('workspace_root') or fallback)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('workspace_root must not contain symlinks')
    for name in ('state', 'assets', 'cache', 'runs'):
        target = root/name
        if target.is_symlink(): raise ValueError('workspace directory must not be a symlink')
        target.mkdir(exist_ok=True, mode=0o700 if name in ('state', 'runs') else 0o755)
    return root


def execution_dirs(root, execution):
    name = str(uuid.UUID(execution))
    directory = root/'runs'/name
    if directory.is_symlink(): raise ValueError('execution directory is a symlink')
    directory.mkdir(exist_ok=True, mode=0o700)
    for name in ('work', 'output'):
        target = directory/name
        if target.is_symlink(): raise ValueError('execution data directory is a symlink')
        target.mkdir(exist_ok=True, mode=0o777)
        # Parent is private to the worker; leaf must support arbitrary image UIDs.
        target.chmod(0o777)
    return directory


def read_events(path, offset):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b''
    import stat
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise RuntimeError('episode journal must be a regular file')
        if os.fstat(f.fileno()).st_size < offset:
            raise RuntimeError('episode journal was truncated')
        f.seek(offset)
        return f.read(1024*1024)


def disk_status(root, config):
    import shutil
    minimum = int(config.get('min_free_disk_gb', 2)*1024)
    if not 0 <= minimum <= 2147483647:
        raise ValueError('invalid min_free_disk_gb')
    return {'free_mb': min(2147483647, shutil.disk_usage(root).free//1048576), 'min_free_mb': minimum}


def checked_path(value):
    """Validate before mkdir or bind: never resolve a symlink into an allowed path."""
    path = Path(value)
    if (not path.is_absolute() or path == Path('/') or '..' in path.parts or ',' in str(value)
            or any(ord(c) < 32 or ord(c) == 127 for c in str(value))):
        raise ValueError('expected an absolute directory without commas, control characters or ..')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'directory contains a symlink: {part}')
    for forbidden in ('/proc', '/sys', '/dev', '/run', '/etc', '/boot', '/root'):
        if path == Path(forbidden) or Path(forbidden) in path.parents:
            raise ValueError(f'sensitive or special directory is not allowed: {path}')
    if any(x in ('.ssh', '.gnupg', '.aws', '.config', '.kube') for x in path.parts):
        raise ValueError(f'credential directory is not allowed: {path}')
    return path


def contains(parent, child):
    return parent == child or parent in child.parents


def storage_paths(config):
    root = Path(config['workspace_root'])
    return {'workspace_root': str(root), 'assets_root': str(config.get('assets_root') or root/'assets'),
            'cache_root': str(config.get('cache_root') or root/'cache'),
            'min_free_disk_gb': config.get('min_free_disk_gb', 2)}


def validate_storage(config, state_path, config_path=None, create=False):
    values = storage_paths(config)
    root, assets, cache = [checked_path(values[k]) for k in ('workspace_root', 'assets_root', 'cache_root')]
    # Sharing a home directory also shares credentials even if they do not exist yet.
    protected = [Path(state_path).absolute(), Path(str(state_path)+'.runs').absolute(), root/'runs',
                 Path.home()/'.ssh', Path.home()/'.config', Path.home()/'.aws', Path.home()/'.gnupg']
    writable = root.parent if root.parent.name == 'slots' else root/'runs'
    protected.append(writable)
    if config_path:
        protected.append(Path(config_path).absolute())
    for shared in (assets, cache):
        if any(contains(shared, p) for p in protected) or contains(writable, shared):
            raise ValueError(f'shared directory overlaps execution state or credentials: {shared}')
        if not shared.is_dir() or not os.access(shared, os.R_OK | os.X_OK):
            raise ValueError(f'shared directory must already exist and be readable: {shared}')
    if contains(assets, root) or contains(cache, root):
        raise ValueError('workspace must not be inside a shared directory')
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        checked_path(root/'runs').mkdir(exist_ok=True, mode=0o700)
    if not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
        raise ValueError(f'workspace is not writable: {root}')
    checked_path(root/'runs')
    import tempfile
    with tempfile.TemporaryFile(dir=root):
        pass
    disk_status(root, values)
    return values


def directory_capacity(config):
    import shutil
    return {name: min(2147483647, shutil.disk_usage(config[key]).free//1048576)
            for name, key in [('workspace', 'workspace_root'), ('assets', 'assets_root'), ('cache', 'cache_root')]}


def probe_storage(config, runner_image, docker):
    """No image pull, network or GPU; bounded container with fixed probe program."""
    import shutil
    identity = str(uuid.uuid4())
    directory = execution_dirs(Path(config['workspace_root']), identity)
    name = 'mini-eval-storage-probe-' + identity
    args = ['run', '--name', name, '--pull', 'never', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '32',
            '--cpus', '0.25', '--memory', '128m', '--user', '65534:65534', '--entrypoint', 'python']
    for source, target, ro in [(config['assets_root'], '/opt/mini-eval/assets', True),
            (config['cache_root'], '/opt/mini-eval/cache', True),
            (str(directory/'work'), '/tmp/mini-eval-work', False),
            (str(directory/'output'), '/tmp/mini-eval-output', False)]:
        args += ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if ro else '')]
    program = '''import os
from pathlib import Path
for p in ('/opt/mini-eval/assets', '/opt/mini-eval/cache'):
    with os.scandir(p) as entries:
        next(entries, None)
    assert os.statvfs(p).f_flag & os.ST_RDONLY, 'shared mount is not read-only'
for p in ('/tmp/mini-eval-work', '/tmp/mini-eval-output'):
    f = Path(p)/'probe'
    f.write_text('storage probe')
    assert f.read_text() == 'storage probe'
    f.unlink()
'''
    try:
        docker(*args, runner_image, '-c', program, timeout=20)
    finally:
        result = docker('rm', '-f', name, check=False, timeout=10)
        if result.returncode and 'No such container' not in result.stderr:
            raise RuntimeError('storage probe container cleanup failed: ' + result.stderr[-500:])
        shutil.rmtree(directory)
