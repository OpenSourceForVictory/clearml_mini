"""Build trusted artifact registrations and demote bounded reader subprocesses."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

from . import artifact_reader as reader


def account_identity(account):
    return {'name': account.pw_name, 'uid': account.pw_uid, 'gid': account.pw_gid,
            'home': account.pw_dir, 'shell': account.pw_shell,
            'groups': sorted(set(os.getgrouplist(account.pw_name, account.pw_gid)))}


def register(root, approved_roots, account, revision):
    """Called only from approved execution setup, never from an API path argument."""
    reader.parts(str(root), absolute=True)
    root = Path(root)
    if not any(root == Path(allowed) or Path(allowed) in root.parents for allowed in approved_roots):
        raise ValueError('artifact root is outside approved directories')
    if type(revision) is not int or revision < 0:
        raise ValueError('invalid approval revision')
    # O_PATH traversal establishes directory identity without changing permissions.
    st = root.lstat()
    identity = [st.st_dev, st.st_ino]
    with reader.directory(str(root), identity):
        pass
    return {'root': str(root), 'root_identity': identity,
            'account': account_identity(account), 'approval_revision': revision}


def read(registration, request, *, approval_revision, timeout=10):
    """Caller supplies the *current* approved revision, not one from the browser.

    The child opens paths only after changing identity. No privileged file
    descriptors or login environment are passed. This also avoids setuid in the
    multithreaded Worker process itself.
    """
    if registration['approval_revision'] != approval_revision:
        return {'status': 'approval_changed'}
    saved = registration['account']
    try:
        account = pwd.getpwnam(saved['name'])
    except KeyError:
        return {'status': 'account_missing'}
    if account_identity(account) != saved:
        return {'status': 'account_changed'}
    identity = {}
    if os.geteuid() == 0:
        identity = {'user': saved['uid'], 'group': saved['gid'], 'extra_groups': saved['groups']}
    elif (os.geteuid() != saved['uid'] or os.getegid() != saved['gid']
          or set(os.getgroups()) | {os.getegid()} != set(saved['groups'])):
        return {'status': 'permission_denied'}
    body = json.dumps({'root': registration['root'], 'root_identity': registration['root_identity'],
                       'request': request}, ensure_ascii=True)
    if len(body.encode()) > 16384:
        return {'status': 'invalid_request'}
    try:
        process = subprocess.run([sys.executable, '-I', str(Path(reader.__file__).resolve())],
            input=body, text=True, capture_output=True, timeout=timeout, cwd='/',
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, close_fds=True, **identity)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout'}
    except OSError:
        return {'status': 'reader_unavailable'}
    if process.returncode:
        return {'status': 'reader_unavailable'}
    try:
        result = json.loads(process.stdout)
        if not isinstance(result, dict) or not isinstance(result.get('status'), str):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        return {'status': 'reader_unavailable'}
