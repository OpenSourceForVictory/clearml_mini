"""Small, durable local state. Never store node credentials or raw subprocess output."""
import fcntl
import json
import os
import threading
from pathlib import Path
from worker.storage import atomic_json


def local_directory(path):
    path = Path(path).absolute()
    if path.resolve() != path: raise ValueError('Agent state path must not contain symlinks')
    # Check existing ancestor against the most specific Linux mount.
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        mount = left.split()[4].replace('\\040', ' ').replace('\\134', '\\')
        if path == Path(mount) or Path(mount) in path.parents:
            mounts.append((len(mount), right.split()[0]))
    if not mounts or max(mounts)[1] in ('nfs', 'nfs4', 'cifs', 'smb3', 'fuse.sshfs'):
        raise ValueError('Agent state requires a local filesystem')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


class State:
    def __init__(self, root):
        self.mutex = threading.RLock()
        self.root = local_directory(root)
        self.lock = (self.root/'agent.lock').open('a')
        try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close(); raise
        self.path = self.root/'state.json'
        try:
            self.value = json.loads(self.path.read_text()) if self.path.exists() else {'revision': 0, 'events': [], 'next_event': 1}
            if (not isinstance(self.value, dict) or not isinstance(self.value.get('events'), list) or len(self.value['events']) > 64
                    or type(self.value.get('revision')) is not int or self.value['revision'] < 0
                    or type(self.value.get('next_event')) is not int or self.value['next_event'] < 1
                    or any(not isinstance(e,dict) or type(e.get('sequence')) is not int or not isinstance(e.get('code'),str) for e in self.value['events'])):
                raise ValueError('invalid Agent recovery state')
        except BaseException:
            self.close(); raise

    def persist(self): atomic_json(self.path, self.value)

    def event(self, code):
        with self.mutex: self._event(code)

    def _event(self, code):
        self.value['events'] = (self.value['events'] + [{'sequence': self.value['next_event'], 'code': code}])[-64:]
        self.value['next_event'] += 1
        self.persist()

    def acknowledge(self, sequence):
        with self.mutex:
            self.value['events'] = [x for x in self.value['events'] if x['sequence'] > sequence]
            self.persist()

    def close(self): self.lock.close()
