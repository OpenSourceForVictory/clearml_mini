"""Durable claim request/receipt handoff; this module never launches a program."""
import copy
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import uuid

from .storage import atomic_json


class RecoveryClaim:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError('claim journal must not be a symlink')
        self.owner = os.open(str(self.path)+'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.record = json.loads(self.path.read_text()) if self.path.exists() else None
            if self.record is not None and self.record.get('version') != 1:
                raise ValueError('unsupported claim journal version')
        except BaseException:
            os.close(self.owner)
            raise

    def close(self):
        if self.owner is not None:
            os.close(self.owner)
            self.owner = None

    def _open(self):
        if self.owner is None:
            raise RuntimeError('claim journal ownership has been closed')

    def _save(self, value):
        self._open()
        atomic_json(self.path, value)
        self.record = value

    def begin(self, *, slot_id, known_execution_ids, requested_at, boot_id):
        self._open()
        if not slot_id or not boot_id or not isinstance(slot_id,str) or not isinstance(boot_id,str):
            raise ValueError('slot and boot identity required')
        if type(requested_at) not in (int,float) or not math.isfinite(requested_at) or requested_at < 0:
            raise ValueError('invalid claim monotonic origin')
        if self.record is not None:
            if self.record['slot_id'] != slot_id:
                raise ValueError('claim belongs to a different slot')
            # Neither a new poll, new boot nor newly discovered journals can
            # rewrite the original request or extend its local time origin.
            return copy.deepcopy(self.record['request'])
        if (not isinstance(known_execution_ids,list) or len(known_execution_ids)>1024
                or any(not isinstance(v,str) or not v for v in known_execution_ids)):
            raise ValueError('invalid known execution list')
        request = {'request_id':str(uuid.uuid4()), 'known_execution_ids':sorted(set(known_execution_ids))}
        self._save({'version':1, 'slot_id':slot_id, 'boot_id':boot_id,
                    'requested_at':requested_at, 'request':request, 'receipt':None})
        return copy.deepcopy(request)

    def accept(self, receipt):
        self._open()
        if not self.record:
            raise ValueError('no pending claim request')
        fields={'request_id','execution_id','generation','policy','digest','launch_authorized','disposition','task'}
        if not isinstance(receipt,dict) or set(receipt)!=fields:
            raise ValueError('invalid recovery claim receipt')
        if receipt['request_id'] != self.record['request']['request_id'] or receipt['launch_authorized'] is not False:
            raise ValueError('claim receipt is not original assignment evidence')
        if any(not isinstance(receipt[k],str) or not 0<len(receipt[k])<=36 for k in ('execution_id','generation')):
            raise ValueError('claim assignment identity missing')
        if not isinstance(receipt['digest'],str) or not re.fullmatch('[0-9a-f]{64}',receipt['digest']):
            raise ValueError('invalid claim snapshot digest')
        policy=receipt['policy']
        if (not isinstance(policy,dict) or set(policy)!={'version','disconnect_tolerance_seconds'}
                or type(policy['version']) is not int or policy['version']!=1):
            raise ValueError('unsupported claim policy')
        tolerance=policy['disconnect_tolerance_seconds']
        if tolerance is not None and (type(tolerance) is not int or not 0<tolerance<2**63):
            raise ValueError('invalid claim disconnection tolerance')
        disposition=receipt['disposition']
        if disposition not in ('reconcile','revoked','deleted','conflict','stop'):
            raise ValueError('invalid claim disposition')
        task=receipt['task']
        if disposition=='reconcile':
            if not isinstance(task,dict) or task.get('execution_id')!=receipt['execution_id']:
                raise ValueError('claim task identity mismatch')
            encoded=json.dumps(task,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            if len(encoded)>1024*1024 or hashlib.sha256(encoded).hexdigest()!=receipt['digest']:
                raise ValueError('claim task snapshot digest mismatch')
        elif task is not None:
            raise ValueError('stopped claim cannot carry a launch snapshot')
        previous=self.record['receipt']
        if previous:
            if any(previous[k]!=receipt[k] for k in ('execution_id','generation','policy','digest')):
                raise ValueError('claim receipt changed its immutable assignment')
            if previous['disposition']!='reconcile' and disposition=='reconcile':
                raise ValueError('stopped claim cannot be restored by an old response')
        self._save({**self.record,'receipt':copy.deepcopy(receipt)})

    def handoff(self, persist_assignment):
        """Caller durably stores by execution ID, then we clear the request.

        Callback must be an idempotent journal write, never process launch. A
        crash after its write may repeat handoff with the identical assignment.
        The receiving journal must require control reconciliation before start.
        """
        self._open()
        if not self.record or self.record['receipt'] is None:
            raise ValueError('claim receipt is not durable')
        record=copy.deepcopy(self.record)
        persist_assignment({'claim':record, 'reconciliation_required':True})
        self._save(None)

    def empty(self, request_id):
        """Clear only an explicitly acknowledged empty allocation response."""
        self._open()
        if (not self.record or self.record['request']['request_id']!=request_id
                or self.record['receipt'] is not None):
            raise ValueError('empty claim acknowledgement conflicts with assignment')
        self._save(None)
