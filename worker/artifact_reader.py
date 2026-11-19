"""Bounded read-only node file helper. Runs as the registered business identity.

Only a trusted local registry supplies roots. The request supplies relative names,
never an absolute path. No imports from business code or shell initialization.
"""
import base64
from contextlib import contextmanager
import errno
import heapq
import hashlib
import json
import os
import stat
import sys

BLOCK_BYTES = 1024 * 1024
PAGE_ITEMS = 100
RESERVED = {'.mini-eval-protocol', '.mini-eval-artifacts'}


class ReadError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def parts(value, absolute=False):
    if not isinstance(value, str) or len(value.encode('utf-8', errors='surrogatepass')) > 4096:
        raise ReadError('invalid_path')
    if any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value) or '\\' in value:
        raise ReadError('invalid_path')
    if absolute:
        if not value.startswith('/') or value == '/':
            raise ReadError('invalid_path')
        value = value[1:]
    elif value.startswith('/'):
        raise ReadError('invalid_path')
    if not value:
        return ()
    items = value.split('/')
    if any(p in ('', '.', '..') or p in RESERVED for p in items):
        raise ReadError('invalid_path')
    return tuple(items)


def version(st):
    return f'{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}'


@contextmanager
def directory(root, expected, relative=''):
    """Search-only ancestors are valid; no symlink may redirect traversal."""
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
    try:
        for name in parts(root, absolute=True):
            child = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        st = os.fstat(fd)
        if [st.st_dev, st.st_ino] != expected:
            raise ReadError('root_changed')
        for name in parts(relative):
            child = os.open(name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def bounded_int(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ReadError('invalid_request')
    return value


def query(root, expected, request):
    if not isinstance(request, dict) or set(request) - {'operation', 'path', 'limit', 'after', 'version', 'offset', 'length'}:
        raise ReadError('invalid_request')
    operation = request.get('operation')
    relative = request.get('path', '')
    components = parts(relative)
    if operation == 'list':
        limit = bounded_int(request.get('limit', PAGE_ITEMS), 1, PAGE_ITEMS)
        after = request.get('after', '')
        if after and (len(parts(after)) != 1):
            raise ReadError('invalid_request')
        if not isinstance(after, str):
            raise ReadError('invalid_request')
        with directory(root, expected, relative) as parent:
            fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                initial = version(os.fstat(fd))
                fingerprint = [0, 0]
                def scan():
                    with os.scandir(fd) as entries:
                        for entry in entries:
                            if entry.name in RESERVED:
                                continue
                            fingerprint[0] += 1
                            fingerprint[1] ^= int.from_bytes(hashlib.sha256(
                                os.fsencode(entry.name) + b'\0' + str(entry.inode()).encode()).digest())
                            yield entry.name
                names = heapq.nsmallest(limit + 1, (name for name in scan() if name > after))
                before = initial + ':' + str(fingerprint)
                if request.get('version') not in (None, before):
                    raise ReadError('file_changed')
                if after and not request.get('version'):
                    raise ReadError('invalid_request')
                items = []
                for name in names[:limit]:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    kind = 'directory' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else 'unsupported'
                    items.append({'name': name, 'kind': kind, 'size_bytes': st.st_size,
                                  'version': version(st)})
                fingerprint = [0, 0]
                for _ in scan():
                    pass
                if version(os.fstat(fd)) + ':' + str(fingerprint) != before:
                    raise ReadError('file_changed')
                return {'status': 'ready', 'items': items, 'version': before,
                        'next': names[limit - 1] if len(names) > limit else None}
            finally:
                os.close(fd)
    if operation not in ('stat', 'read') or not components:
        raise ReadError('invalid_request')
    with directory(root, expected, '/'.join(components[:-1])) as parent:
        anchor = os.open(components[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            st = os.fstat(anchor)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise ReadError('unsupported_file')
            # Reopen the pinned regular inode, never a possibly swapped pathname.
            fd = os.open(f'/proc/self/fd/{anchor}', os.O_RDONLY | os.O_NONBLOCK)
        finally:
            os.close(anchor)
        try:
            st = os.fstat(fd)
            current = version(st)
            if request.get('version') not in (None, current):
                raise ReadError('file_changed')
            result = {'status': 'ready', 'size_bytes': st.st_size, 'version': current}
            if operation == 'read':
                # Reading requires the stat/list version, including the first range.
                if request.get('version') != current:
                    raise ReadError('invalid_request')
                offset = bounded_int(request.get('offset', 0), 0, st.st_size)
                length = bounded_int(request.get('length', BLOCK_BYTES), 1, BLOCK_BYTES)
                content = os.pread(fd, min(length, st.st_size - offset), offset)
                if version(os.fstat(fd)) != current:
                    raise ReadError('file_changed')
                result.update(offset=offset, data=base64.b64encode(content).decode('ascii'))
            return result
        finally:
            os.close(fd)


def respond(root, expected, request):
    try:
        return query(root, expected, request)
    except ReadError as exc:
        return {'status': exc.status}
    except OSError as exc:
        status = {errno.ENOENT: 'missing', errno.EACCES: 'permission_denied',
                  errno.EPERM: 'permission_denied', errno.ELOOP: 'unsafe_path',
                  errno.ENOTDIR: 'unsafe_path'}.get(exc.errno, 'io_error')
        return {'status': status}


def main():
    # Input is a bounded, trusted Worker message, not business stdout or a shell.
    value = json.loads(sys.stdin.buffer.read(16385))
    result = respond(value['root'], value['root_identity'], value['request'])
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
