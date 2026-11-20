"""Directory-local episode metadata; never an authorization or file path source."""
import json
import os
import stat
import uuid
from pathlib import Path

NAME = '.mini-eval-artifacts'
MAX_BYTES = 1024


def read(fd):
    """Return an explicit directory attribution, ignoring absent/invalid metadata."""
    handle = None
    try:
        handle = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        st = os.fstat(handle)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > MAX_BYTES:
            return None
        value = json.loads(os.read(handle, MAX_BYTES + 1))
        if (type(value) is dict and set(value) == {'schema_version', 'episode_id'}
                and type(value['schema_version']) is int and value['schema_version'] == 1
                and type(value['episode_id']) is int and 1 <= value['episode_id'] <= 10000):
            return value['episode_id']
    except (OSError, ValueError, UnicodeError):
        pass
    finally:
        if handle is not None:
            os.close(handle)
    return None


def register(directory, episode_id):
    """Best-effort atomic registration, without following directory/file links.

    A metadata failure must not change the business result. No process-global
    environment is modified and no shared execution-wide ledger is required.
    """
    if type(episode_id) is not int or not 1 <= episode_id <= 10000:
        return False
    fd = None
    temporary = None
    try:
        path = Path(directory)
        if not path.is_absolute() or '..' in path.parts:
            return False
        fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
        for component in path.parts[1:]:
            child = os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        existing = read(fd)
        if existing is not None:
            return existing == episode_id
        temporary = NAME + '-' + uuid.uuid4().hex
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        with os.fdopen(handle, 'wb') as out:
            out.write(json.dumps({'schema_version': 1, 'episode_id': episode_id}).encode())
            out.flush()
            os.fsync(out.fileno())
        try:
            os.link(temporary, NAME, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        except FileExistsError:
            return read(fd) == episode_id
        os.unlink(temporary, dir_fd=fd)
        temporary = None
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            if temporary:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except OSError:
                    pass
            os.close(fd)
