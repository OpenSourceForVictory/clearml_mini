"""Durable at-most-once launch boundary; not enabled in the legacy Worker loop."""
import copy
import fcntl
import json
import os
from pathlib import Path
import threading
import uuid

from .recovery_supervisor import identity, timestamp
from .storage import atomic_json


class RecoveryLaunch:
    def __init__(self, path, *, execution_id, generation, request_id, boot_id, kind):
        if not execution_id or not generation or not boot_id or kind not in ('host','docker'):
            raise ValueError('complete launch assignment required')
        request_id = str(uuid.UUID(request_id))
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('launch journal must not be a symlink')
        self._lock = threading.RLock()
        self.owner = os.open(str(self.path)+'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assignment = dict(execution_id=execution_id, generation=generation, request_id=request_id,
                              boot_id=boot_id, kind=kind)
            if self.path.exists():
                self.record = json.loads(self.path.read_text())
                if self.record['version'] != 1 or self.record['assignment'] != assignment:
                    raise ValueError('launch assignment or boot changed; reconciliation required')
            else:
                self._save(dict(version=1, assignment=assignment, launch_id=str(uuid.uuid4()),
                    phase='prepared', requested_at=None, reply=None, started_at=None, identity=None))
        except BaseException:
            os.close(self.owner)
            raise

    def close(self):
        with self._lock:
            if self.owner is not None:
                os.close(self.owner)
                self.owner = None

    def _open(self):
        if self.owner is None:
            raise RuntimeError('launch journal ownership has been closed')

    def _save(self, record):
        self._open()
        atomic_json(self.path, record)
        self.record = record

    def request(self, now):
        with self._lock:
            self._open();timestamp(now)
            if self.record['phase'] != 'prepared':
                raise RuntimeError('launch already attempted or blocked; reconcile instead')
            if self.record['requested_at'] is None:
                self._save({**self.record, 'requested_at':now})
            return {k:v for k,v in {**self.record['assignment'], 'launch_id':self.record['launch_id']}.items()
                    if k != 'execution_id'}

    def accept(self, reply, now):
        with self._lock:
            self._open();timestamp(now)
            r = self.record
            if r['phase'] != 'prepared' or r['requested_at'] is None:
                raise ValueError('no pending launch confirmation')
            expected = {**r['assignment'], 'launch_id':r['launch_id']}
            fields = set(expected) | {'action','reason','duplicate','valid_for_seconds'}
            if (not isinstance(reply,dict) or set(reply)!=fields
                    or any(reply[k]!=v for k,v in expected.items())
                    or reply['action'] not in ('launch','stop','inspect')
                    or type(reply['duplicate']) is not bool
                    or type(reply['valid_for_seconds']) not in (int,float) or not 0<reply['valid_for_seconds']<=30):
                raise ValueError('invalid launch confirmation')
            if now < r['requested_at']:
                raise ValueError('monotonic clock moved backwards')
            phase = 'prepared' if reply['action']=='launch' and now<r['requested_at']+reply['valid_for_seconds'] else 'blocked'
            self._save({**r, 'reply':copy.deepcopy(reply), 'phase':phase})
            return phase=='prepared'

    def start(self, now, launch):
        """Persist attempt before callback. Any callback failure requires inspection.

        Callback must attach launch_id to its runtime metadata. It cannot be
        retried just because its response or the subsequent identity write was
        lost. Its runtime must retain exit/identity evidence for recovery.
        """
        with self._lock:
            self._open();timestamp(now)
            r = self.record
            if r['phase']!='prepared' or not r['reply'] or r['reply']['action']!='launch':
                raise RuntimeError('launch is not confirmed or was already attempted')
            if now<r['requested_at'] or now>=r['requested_at']+r['reply']['valid_for_seconds']:
                self._save({**r,'phase':'blocked'})
                raise RuntimeError('launch confirmation expired')
            self._save({**r, 'phase':'attempted', 'started_at':now})
            observed = launch(r['launch_id'])
            return self.bind(observed)

    def bind(self, observed):
        """Bind only a runtime proven to carry this exact launch ID, not a name."""
        with self._lock:
            self._open()
            r=self.record
            if r['phase'] not in ('attempted','bound'):
                raise RuntimeError('no durable launch attempt')
            if not isinstance(observed,dict) or observed.get('launch_id')!=r['launch_id']:
                raise ValueError('runtime launch marker missing or mismatched')
            process=identity(observed.get('identity'))
            if any(process[k]!=r['assignment'][k] for k in ('kind','boot_id')):
                raise ValueError('runtime belongs to another boot or mode')
            if r['identity'] is not None and r['identity']!=process:
                raise ValueError('original runtime identity changed')
            self._save({**r,'phase':'bound','identity':process})
            return copy.deepcopy(self.record)
