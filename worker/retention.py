"""Linux-only, fail-closed execution inventory and recoverable deletion.

Host administrators are trusted. Untrusted containers must not mount the Worker
state or the private staging directory. No paths supplied by the API are opened.
"""
import contextlib
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import threading
import time
import uuid
from datetime import datetime, timezone
from .storage import atomic_json

LOG = logging.getLogger(__name__)
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Protected(RuntimeError):
    pass


def mount_id(fd):
    for line in Path(f'/proc/self/fdinfo/{fd}').read_text().splitlines():
        if line.startswith('mnt_id:'):
            return int(line.split(':', 1)[1])
    raise Protected('Linux mount identity is unavailable')


def open_path(path):
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        raise Protected('invalid trusted root')
    fd = os.open('/', FLAGS)
    try:
        for part in p.parts[1:]:
            child = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def identity(fd):
    s = os.fstat(fd)
    return s.st_dev, s.st_ino


def same(fd, dev, ino):
    if identity(fd) != (dev, ino):
        raise Protected('directory identity changed')


def child_dir(parent, name, mount):
    fd = os.open(name, FLAGS, dir_fd=parent)
    if mount_id(fd) != mount:
        os.close(fd)
        raise Protected('nested mount (including bind mount) is protected')
    return fd


def private_dir(parent, name):
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = child_dir(parent, name, mount_id(parent))
    s = os.fstat(fd)
    if s.st_uid != os.geteuid() or stat.S_IMODE(s.st_mode) != 0o700:
        os.close(fd)
        raise Protected('private directory ownership or permissions changed')
    return fd


def tree(fd):
    """Yield per entry; return a stable fingerprint and hard-link-deduped sizes.

    Root ctime is excluded because rename changes it. Each descendant includes
    identity, type, mtime/ctime, size and blocks. Symlink targets are not followed.
    """
    mount = mount_id(fd)
    seen, logical, allocated, digest = set(), 0, 0, 0
    def walk(directory, prefix):
        nonlocal logical, allocated, digest
        with os.scandir(directory) as entries:
            for entry in entries:
                s = entry.stat(follow_symlinks=False)
                rel = prefix + '/' + entry.name
                value = [rel, s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_blocks]
                digest = (digest + int(hashlib.sha256(json.dumps(value).encode()).hexdigest(), 16)) % (1 << 256)
                key = (s.st_dev, s.st_ino)
                if key not in seen:
                    seen.add(key)
                    logical += s.st_size
                    allocated += s.st_blocks * 512
                yield None
                if stat.S_ISDIR(s.st_mode):
                    child = child_dir(directory, entry.name, mount)
                    try:
                        same(child, s.st_dev, s.st_ino)
                        yield from walk(child, rel)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(s.st_mode):
                    # Even a file can be a same-filesystem bind mount.
                    child = os.open(entry.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        if mount_id(child) != mount:
                            raise Protected('mounted file is protected')
                        same(child, s.st_dev, s.st_ino)
                    finally:
                        os.close(child)
        yield None
    yield from walk(fd, '')
    return {'fingerprint': f'{digest:064x}', 'logical_bytes': logical, 'allocated_bytes': allocated}


def consume(generator, stop=None):
    count = 0
    deadline = time.monotonic()+0.1
    while True:
        try:
            next(generator)
        except StopIteration as result:
            return result.value
        count += 1
        if count >= 500 or time.monotonic() >= deadline:
            if stop and stop.is_set():
                generator.close()
                raise Protected('scan interrupted; no complete estimate')
            time.sleep(0.01)
            count, deadline = 0, time.monotonic()+0.1


def remove_tree(fd, progress):
    mount = mount_id(fd)
    with os.scandir(fd) as entries:
        for entry in entries:
            s = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(s.st_mode):
                child = child_dir(fd, entry.name, mount)
                try:
                    same(child, s.st_dev, s.st_ino)
                    remove_tree(child, progress)
                    current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != identity(child):
                        raise Protected('directory replaced during deletion')
                    os.rmdir(entry.name, dir_fd=fd)
                finally:
                    os.close(child)
            else:
                child = os.open(entry.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    same(child, s.st_dev, s.st_ino)
                    if mount_id(child) != mount:
                        raise Protected('mounted entry is protected')
                    current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != identity(child):
                        raise Protected('entry replaced during deletion')
                    os.unlink(entry.name, dir_fd=fd)
                finally:
                    os.close(child)
            progress()
    os.fsync(fd)


class Retention:
    def __init__(self, worker, docker):
        self.worker, self.docker = worker, docker
        self.directory = Path(str(worker.path)+'.retention')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.history_path = self.directory/'roots.json'
        self.roots = json.loads(self.history_path.read_text()) if self.history_path.exists() else {}
        self.guard = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.scan_revision = -1
        self.next_scan = 0
        self.remember(worker.storage_snapshot['config']['workspace_root'])
        for child in worker.children.values():
            self.remember(child.storage_snapshot['config']['workspace_root'])

    def remember(self, workspace):
        path = str(Path(workspace)/'runs')
        try:
            fd = open_path(path)
        except FileNotFoundError:
            return
        try:
            dev, ino = identity(fd)
            key = hashlib.sha256(f'{path}:{dev}:{ino}'.encode()).hexdigest()
            with self.guard:
                if key not in self.roots:
                    self.roots[key] = {'path': path, 'dev': dev, 'ino': ino}
                    atomic_json(self.history_path, self.roots)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def root(self, root_id):
        with self.guard:
            root = dict(self.roots.get(root_id) or {})
        if not root:
            raise Protected('root has no durable trusted history')
        fd = open_path(root['path'])
        try:
            same(fd, root['dev'], root['ino'])
            yield fd, root
        finally:
            os.close(fd)

    def local_ready(self, execution, path):
        w = self.worker
        if execution in w.children or (w.threads.get(execution) and w.threads[execution].is_alive()):
            raise Protected('local execution thread is active')
        journals = [w.path, Path(str(w.path)+'.runs')/(execution+'.json')]
        for journal in journals:
            if journal.exists():
                value = json.loads(journal.read_text())
                if value and (journal != w.path or value.get('task', {}).get('execution_id') == execution):
                    raise Protected('execution recovery journal is still active')
        containers = self.docker('ps', '-aq').stdout.split()
        for start in range(0, len(containers), 100):
            for container in json.loads(self.docker('inspect', *containers[start:start+100]).stdout):
                if (container.get('Config', {}).get('Labels') or {}).get('mini-eval.execution') == execution:
                    raise Protected('execution container has not been removed')
                for mount in container.get('Mounts', []):
                    source = mount.get('Source')
                    if source:
                        a, b = Path(source).resolve(), Path(path).resolve()
                        if a == b or a in b.parents or b in a.parents:
                            raise Protected('a Docker container references the directory')

    def mark_ready(self, execution):
        atomic_json(self.directory/('ready-'+execution+'.json'), {'execution_id': execution, 'at': time.time()})

    def scan_entry(self, root_id, name, location='runs'):
        return consume(self.scan_entry_steps(root_id, name, location), self.stopping)

    def scan_entry_steps(self, root_id, name, location='runs'):
        result = {'root_id': root_id, 'root_path': self.roots[root_id]['path'], 'entry_name': name,
                  'generation': str(uuid.uuid4()), 'scan_status': 'blocked', 'reason': None}
        try:
            if str(uuid.UUID(name)) != name:
                raise ValueError()
        except ValueError:
            result['reason'] = 'unknown directory; not an execution UUID'
            return result
        try:
            with self.root(root_id) as (rootfd, root):
                parent = private_dir(rootfd, '.cleanup') if location == 'staging' else os.dup(rootfd)
                try:
                    fd = child_dir(parent, name, mount_id(rootfd))
                    try:
                        before = os.fstat(fd)
                        value = yield from tree(fd)
                        after = os.fstat(fd)
                        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                            raise Protected('directory changed during scan')
                        result.update(scan_status='complete', logical_bytes=value['logical_bytes'], allocated_bytes=value['allocated_bytes'],
                            identity={'root_dev': root['dev'], 'root_ino': root['ino'], 'dev': before.st_dev, 'ino': before.st_ino,
                                      'fingerprint': value['fingerprint'], 'location': location})
                        self.local_ready(name, Path(root['path'])/name)
                        self.mark_ready(name)
                    finally:
                        os.close(fd)
                finally:
                    os.close(parent)
        except Exception as exc:
            result['scan_status'], result['reason'] = 'blocked', str(exc)[:1000]
        return result

    def scan(self):
        # Stream directories; each report is bounded, and scans yield independently
        # from the supervisor's heartbeat. Unknown directories are never traversed.
        for root_id in list(self.roots):
            try:
                with self.root(root_id) as (fd, _):
                    for location in ('runs', 'staging'):
                        parent = private_dir(fd, '.cleanup') if location == 'staging' else os.dup(fd)
                        try:
                            with os.scandir(parent) as entries:
                                for entry in entries:
                                    if self.stopping.is_set():
                                        return
                                    if entry.name in ('.cleanup', '.retention-locks'):
                                        continue
                                    if location == 'staging' and not self.failed_staging(root_id, entry.name):
                                        continue
                                    self.post('inventory', {'entries': [{'root_id': root_id, 'root_path': self.roots[root_id]['path'],
                                        'entry_name': entry.name, 'generation': str(uuid.uuid4()), 'scan_status': 'partial', 'reason': 'scan in progress'}]})
                                    result = yield from self.scan_entry_steps(root_id, entry.name, location)
                                    self.post('inventory', {'entries': [result]})
                                    yield None
                        finally:
                            os.close(parent)
            except Exception as exc:
                LOG.exception('execution inventory root unavailable')
                self.post('inventory', {'entries': [{'root_id': root_id, 'root_path': self.roots[root_id]['path'],
                    'entry_name': '<root-unavailable>', 'generation': str(uuid.uuid4()), 'scan_status': 'blocked',
                    'reason': str(exc)[:1000]}]})

    def failed_staging(self, root_id, name):
        for path in self.directory.glob('item-*.json'):
            record = json.loads(path.read_text())
            if (record['snapshot']['root_id'] == root_id and record['snapshot']['entry_name'] == name
                    and record.get('receipt', {}).get('status') == 'failed'):
                return True
        return False

    def post(self, suffix, body=None):
        return self.worker.api.post(f'worker/slots/{self.worker.slot_id}/execution-data/{suffix}', body)

    def fault(self, phase):
        """Tests replace this hook with a simulated process crash."""

    def execute(self, item):
        try:
            return self.execute_checked(item)
        except Exception as exc:
            item_id = str(uuid.UUID(item['id']))
            path = self.directory/('item-'+item_id+'.json')
            record = json.loads(path.read_text()) if path.exists() else None
            # Network failure after a durable receipt is retried unchanged.
            if record and record.get('receipt'):
                raise
            if record:
                # An accepted operation keeps its recovery journal on transient
                # root/network errors. Never convert uncertain I/O into success.
                LOG.warning('accepted cleanup awaits recovery: %s', exc)
                raise
            record = {'snapshot': item['snapshot'], 'phase': 'rejected', 'deleted_entries': 0,
                      'receipt': {'status': 'skipped', 'reason': str(exc)[:1000],
                                  'free_before': None, 'free_after': None, 'deleted_entries': 0}}
            atomic_json(path, record)
            self.post(item_id+'/receipt', record['receipt'])

    def execute_checked(self, item):
        item_id = str(uuid.UUID(item['id']))
        journal = self.directory/('item-'+item_id+'.json')
        record = json.loads(journal.read_text()) if journal.exists() else None
        if record and record.get('receipt'):
            self.post(item_id+'/receipt', record['receipt'])
            return
        snapshot = record['snapshot'] if record else item['snapshot']
        name = snapshot['entry_name']
        if str(uuid.UUID(name)) != name or snapshot['execution_id'] != name:
            raise Protected('invalid execution identity')
        expected = snapshot['identity']
        with self.root(snapshot['root_id']) as (rootfd, root):
            same(rootfd, expected['root_dev'], expected['root_ino'])
            locks = private_dir(rootfd, '.retention-locks')
            lockfd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=locks)
            os.close(locks)
            try:
                fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._execute_locked(item_id, journal, record, snapshot, rootfd, root)
            finally:
                os.close(lockfd)

    def _execute_locked(self, item_id, journal, record, snapshot, rootfd, root):
        name, expected = snapshot['entry_name'], snapshot['identity']
        stage = private_dir(rootfd, '.cleanup')
        fd = None
        try:
            if record is None:
                with self.worker.start_lock:
                    self.local_ready(name, Path(root['path'])/name)
                    if expected['location'] == 'staging' and not self.failed_staging(snapshot['root_id'], name):
                        raise Protected('staging directory has no failed operation')
                    parent = stage if expected['location'] == 'staging' else rootfd
                    fd = child_dir(parent, name, mount_id(rootfd))
                    same(fd, expected['dev'], expected['ino'])
                    value = consume(tree(fd), self.stopping)
                    if value['fingerprint'] != expected['fingerprint']:
                        raise Protected('directory contents changed since preview')
                    authorization = self.post(item_id+'/begin')
                    if authorization['snapshot'] != snapshot:
                        raise Protected('authorization identity mismatch')
                    if datetime.fromisoformat(authorization['authorized_until']).astimezone(timezone.utc) <= datetime.now(timezone.utc):
                        raise Protected('start authorization expired')
                    record = {'snapshot': snapshot, 'phase': 'intent', 'deleted_entries': 0,
                              'free_before': os.fstatvfs(rootfd).f_bavail * os.fstatvfs(rootfd).f_frsize}
                    atomic_json(journal, record)
                    self.fault('intent')
            if record['phase'] == 'intent':
                # Recovery uses inode identity, never assumes that the source path
                # still denotes the original directory.
                source = os.stat(name, dir_fd=rootfd, follow_symlinks=False) if self.exists(rootfd, name) else None
                target = os.stat(name, dir_fd=stage, follow_symlinks=False) if self.exists(stage, name) else None
                if target:
                    if (target.st_dev, target.st_ino) != (expected['dev'], expected['ino']) or source:
                        raise Protected('source/staging identity conflict')
                elif source and (source.st_dev, source.st_ino) == (expected['dev'], expected['ino']):
                    os.rename(name, name, src_dir_fd=rootfd, dst_dir_fd=stage)
                    os.fsync(rootfd)
                    os.fsync(stage)
                    self.fault('rename')
                else:
                    raise Protected('authorized source directory is missing or replaced')
                record['phase'] = 'staged'
                atomic_json(journal, record)
            if fd is None and record['phase'] not in ('removed', 'empty'):
                fd = child_dir(stage, name, mount_id(rootfd))
                same(fd, expected['dev'], expected['ino'])
            if record['phase'] == 'staged':
                value = consume(tree(fd), self.stopping)
                if value['fingerprint'] != expected['fingerprint']:
                    raise Protected('contents changed during takeover; preserved in staging')
                record['phase'] = 'deleting'
                atomic_json(journal, record)
                self.fault('staged')
            if record['phase'] == 'deleting':
                def progress():
                    record['deleted_entries'] += 1
                    if record['deleted_entries'] % 100 == 0:
                        atomic_json(journal, record)
                        time.sleep(0.01)
                    self.fault('delete')
                remove_tree(fd, progress)
                current = os.stat(name, dir_fd=stage, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != identity(fd):
                    raise Protected('staging identity replaced')
                # Persist intent to remove the empty directory. Recovery may find
                # it absent after a crash, but will never inspect/delete source.
                record['phase'] = 'empty'
                atomic_json(journal, record)
            if record['phase'] == 'empty':
                if self.exists(stage, name):
                    if fd is None:
                        fd = child_dir(stage, name, mount_id(rootfd))
                    same(fd, expected['dev'], expected['ino'])
                    os.rmdir(name, dir_fd=stage)
                    os.fsync(stage)
                record['phase'] = 'removed'
                atomic_json(journal, record)
                self.fault('removed')
            record['receipt'] = {'status': 'succeeded', 'reason': None, 'free_before': record['free_before'],
                'free_after': os.fstatvfs(rootfd).f_bavail * os.fstatvfs(rootfd).f_frsize, 'deleted_entries': record['deleted_entries']}
        except Exception as exc:
            record = record or {'snapshot': snapshot, 'phase': 'rejected', 'deleted_entries': 0}
            record['receipt'] = {'status': 'failed' if record['phase'] != 'rejected' else 'skipped',
                'reason': str(exc)[:1000], 'free_before': record.get('free_before'),
                'free_after': os.fstatvfs(rootfd).f_bavail * os.fstatvfs(rootfd).f_frsize, 'deleted_entries': record['deleted_entries']}
        finally:
            if fd is not None:
                os.close(fd)
            os.close(stage)
        atomic_json(journal, record)
        self.fault('receipt')
        self.post(item_id+'/receipt', record['receipt'])

    @staticmethod
    def exists(fd, name):
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def loop(self):
        scanner = None
        while not self.stopping.is_set():
            try:
                # Accepted local intent can recover offline. Receipts remain durable.
                for path in self.directory.glob('item-*.json'):
                    record = json.loads(path.read_text())
                    if not record.get('acknowledged'):
                        try:
                            self.execute({'id': path.stem[5:], 'snapshot': record['snapshot']})
                            record = json.loads(path.read_text())
                            record['acknowledged'] = True
                            atomic_json(path, record)
                        except Exception:
                            LOG.exception('cleanup receipt/recovery deferred')
                control = self.post('control')
                for item in control['items']:
                    if self.stopping.is_set():
                        break
                    self.execute(item)
                if scanner is None and (control['scan_revision'] != self.scan_revision or time.monotonic() >= self.next_scan):
                    scanner = self.scan()
                    self.scan_revision = control['scan_revision']
                if scanner is not None:
                    deadline = time.monotonic()+0.2
                    for _ in range(1000):
                        try:
                            next(scanner)
                        except StopIteration:
                            scanner = None
                            self.next_scan = time.monotonic()+3600
                            break
                        if time.monotonic() >= deadline:
                            break
            except Exception:
                LOG.exception('execution data maintenance deferred')
            self.stopping.wait(0.1 if scanner is not None else 5)
        if scanner is not None:
            scanner.close()

    def start(self):
        self.thread = threading.Thread(target=self.loop, name='execution-data', daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=10)
