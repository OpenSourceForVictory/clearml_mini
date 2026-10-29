"""Administrator-owned workspace layout. Job payloads never select host paths."""
import os
from pathlib import Path
import uuid


def prepare(config, fallback):
    root = Path(config.get('workspace_root') or fallback)
    if not root.is_absolute() or ',' in str(root) or '\n' in str(root):
        raise ValueError('workspace_root must be an absolute Docker mount path')
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
