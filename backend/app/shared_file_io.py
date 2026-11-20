"""Isolated bounded shared-filesystem I/O, with no database or Worker RPC."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import threading

from fastapi import HTTPException
from worker import artifact_reader as reader

_active = {}
_streams = {}
_lock = threading.Lock()


def filesystem(mapping, relative, query):
    validated = False
    try:
        root = mapping['api_root']
        st = os.stat(root, follow_symlinks=False)
        identity = [st.st_dev, st.st_ino]
        if not mapping.get('automatic'):
            marker = reader.respond(root, identity, {'operation': 'stat', 'path': '.mini-eval-storage-id'})
            if marker.get('status') != 'ready' or marker['size_bytes'] > 130:
                return {'status': 'storage_unavailable'}
            marker = reader.respond(root, identity, {'operation': 'read', 'path': '.mini-eval-storage-id',
                                    'version': marker['version'], 'length': 130})
            if marker.get('status') != 'ready' or base64.b64decode(marker['data']).decode().strip() != mapping['marker']:
                return {'status': 'storage_unavailable'}
        validated = True
        reader.parts(relative)
        with reader.directory(root, identity, relative) as fd:
            st = os.fstat(fd)
            output_identity = [st.st_dev, st.st_ino]
        return reader.respond(root + '/' + relative, output_identity, query)
    except FileNotFoundError:
        return {'status': 'missing' if validated else 'storage_unavailable'}
    except PermissionError:
        return {'status': 'permission_denied' if validated else 'storage_unavailable'}
    except (OSError, ValueError, UnicodeError, reader.ReadError):
        return {'status': 'unsafe_path' if validated else 'storage_unavailable'}


def main():
    value = json.loads(sys.stdin.buffer.read(32769))
    print(json.dumps(filesystem(value['mapping'], value['relative'], value['query']), ensure_ascii=True))


def reserve(user, *, streams=False):
    active = _streams if streams else _active
    with _lock:
        if sum(active.values()) >= 8 or active.get(user, 0) >= 2:
            raise HTTPException(429, '共享文件读取繁忙，请稍后重试', headers={'Retry-After': '2'})
        active[user] = active.get(user, 0) + 1
    def release(_=None):
        with _lock:
            active[user] -= 1
            if not active[user]:
                del active[user]
    return release


async def read(mapping, relative, query, user):
    release = reserve(user)
    process = None
    try:
        body = json.dumps(dict(mapping=mapping, relative=relative, query=query)).encode()
        if len(body) > 32768:
            raise HTTPException(400, '文件请求过大')
        source = str(Path(__file__).resolve().parents[2])
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c',
            'import sys;sys.path.insert(0,sys.argv[1]);from backend.app.shared_file_io import main;main()', source,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd='/', env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        stdout, _ = await asyncio.wait_for(process.communicate(body), timeout=10)
        if process.returncode or len(stdout) > 2 * 1024 * 1024:
            raise HTTPException(503, '共享文件读取进程不可用')
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError('invalid helper response')
        errors = {'storage_unavailable': (503, '共享存储未挂载、身份不匹配或无访问权限'),
                  'missing': (410, '文件不存在或已清理'), 'permission_denied': (403, 'API 服务账号没有文件读取权限'),
                  'file_changed': (409, '文件或目录已变化，请刷新后重试'), 'root_changed': (409, '输出目录已变化'),
                  'unsafe_path': (403, '拒绝读取链接或不安全路径'), 'unsupported_file': (403, '仅支持普通文件'),
                  'invalid_path': (400, '非法文件相对路径'), 'invalid_request': (400, '非法文件读取请求')}
        if result.get('status') != 'ready':
            code, message = errors.get(result.get('status'), (503, '共享文件读取失败'))
            raise HTTPException(code, message)
        return result
    except asyncio.TimeoutError:
        raise HTTPException(504, '共享存储读取超时，请检查存储状态') from None
    except (OSError, ValueError):
        raise HTTPException(503, '共享文件读取进程不可用') from None
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            # An uninterruptible NFS syscall retains its admission slot until the
            # OS actually reaps it; a timeout must not permit unlimited children.
            waiter = asyncio.create_task(process.wait())
            waiter.add_done_callback(release)
        else:
            release()
