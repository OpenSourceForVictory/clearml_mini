"""Local supervision independent of recovery HTTP; no launch or resource release.

Only an explicitly negotiated, durably bound execution may use this supervisor.
The legacy Worker loop must not infer this policy from a missing lease field.
"""
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from .disconnection import DisconnectionClock
from .recovery_control import RecoveryControl
from .storage import atomic_json


def identity(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'boot_id', 'runtime_id'}:
        raise ValueError('complete process identity required')
    size = {'host': 32, 'docker': 64}.get(value['kind'])
    if (not size or not isinstance(value['boot_id'], str) or not 0 < len(value['boot_id']) <= 128
            or not isinstance(value['runtime_id'], str)
            or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value['runtime_id'])):
        raise ValueError('invalid process identity')
    return dict(value)


def timestamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid monotonic timestamp')
    return value


class RecoverySupervisor:
    def __init__(self, path, *, execution_id, generation, process_identity,
                 tolerance, started_at, requested_at, timeout_seconds=0):
        self.path = Path(path)
        process_identity = identity(process_identity)
        timestamp(started_at); timestamp(requested_at)
        if type(timeout_seconds) is not int or timeout_seconds < 0:
            raise ValueError('invalid business timeout')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('supervisor journal must not be a symlink')
        self._lock = threading.RLock()
        self._exchange = threading.Lock()
        self._owner = os.open(str(self.path)+'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                record = json.loads(self.path.read_text())
                if (record['version'] != 1 or record['execution_id'] != execution_id
                        or record['generation'] != generation or record['identity'] != process_identity
                        or record['timeout_seconds'] != timeout_seconds
                        or record['control']['clock']['disconnect_tolerance_seconds'] != tolerance):
                    raise ValueError('persisted supervision identity or policy changed')
                # Original clocks and runtime binding always win over a caller's
                # new construction time after restart.
                RecoveryControl.restore(record['control'], execution_id=execution_id,
                    generation=generation, boot_id=process_identity['boot_id'])
                self.record = record
            else:
                control = RecoveryControl(DisconnectionClock(execution_id, generation,
                    process_identity['boot_id'], tolerance, requested_at))
                self._commit({'version':1, 'execution_id':execution_id, 'generation':generation,
                    'identity':process_identity, 'started_at':started_at, 'last_observed_at':started_at,
                    'timeout_seconds':timeout_seconds, 'control':control.snapshot(),
                    'state':'running', 'exit_code':None, 'stop_reason':None, 'blocked_reason':None,
                    'pending':None, 'sequence':1})
        except BaseException:
            os.close(self._owner)
            raise

    def close(self):
        with self._lock:
            if self._owner is not None:
                os.close(self._owner)
                self._owner = None

    def _check_owner(self):
        if self._owner is None:
            raise RuntimeError('recovery supervisor ownership has been closed')

    def _commit(self, record):
        self._check_owner()
        atomic_json(self.path, record)
        self.record = record

    def snapshot(self):
        with self._lock:
            self._check_owner()
            return copy.deepcopy(self.record)

    def _control(self):
        r = self.record
        return RecoveryControl.restore(r['control'], execution_id=r['execution_id'],
            generation=r['generation'], boot_id=r['identity']['boot_id'])

    def observe(self, process_identity, state, exit_code=None, *, at):
        """Persist facts even offline; return intent, never a cleanup ACK."""
        timestamp(at)
        if state not in ('running', 'exited', 'missing'):
            raise ValueError('invalid process state')
        if exit_code is not None and (state != 'exited' or type(exit_code) is not int):
            raise ValueError('invalid process exit code')
        with self._lock:
            r = self.snapshot()
            if at < r['last_observed_at']:
                raise ValueError('monotonic observation moved backwards')
            if (process_identity != r['identity'] and state != 'missing') or (
                    r['state'] in ('exited','missing') and state == 'running') or (
                    r['state'] == 'exited' and state == 'exited' and r['exit_code'] != exit_code):
                r['blocked_reason'] = 'process_identity_or_completion_changed'
            if r['blocked_reason']:
                self._commit(r)
                return 'inspect'
            control = self._control()
            # A late observation of an already finished process does not prove
            # that it ran past either deadline. Keep actual stop decisions only.
            local_stop = control.clock.check(at) if state == 'running' else None
            r['control'] = control.snapshot()
            r['stop_reason'] = r['stop_reason'] or local_stop
            if state == 'running' and r['timeout_seconds'] and at >= r['started_at'] + r['timeout_seconds']:
                r['stop_reason'] = r['stop_reason'] or 'execution_timeout'
            # An unloaded unit/container cannot erase an already observed exit.
            if r['state'] == 'exited' and state == 'missing':
                state, exit_code = r['state'], r['exit_code']
            r.update(state=state, exit_code=exit_code, last_observed_at=at)
            if state != 'running':
                action = 'retain'
            elif control.directive in ('inspect', 'retain'):
                r['blocked_reason'] = control.reason or 'platform_identity_conflict'
                action = 'inspect'
            elif r['stop_reason'] or control.directive == 'stop':
                action = 'stop'
            else:
                action = 'track'
            self._commit(r)
            return action

    def tick(self, inspect, stop, *, at):
        """Local watchdog pass. inspect/stop must verify the bound runtime ID.

        Call independently of exchange(), including while HTTP is blocked.
        Failed stop retains evidence and is retried by the next local pass.
        """
        value = inspect()
        action = self.observe(value.get('identity'), value['state'], value.get('exit_code'), at=at)
        if action == 'stop':
            # Ownership cannot be released between the check and local stop.
            # This bounded runtime operation never includes recovery HTTP.
            with self._lock:
                stop(self.snapshot()['identity'])
        return action

    def exchange(self, api, *, clock=time.monotonic):
        """One bounded HTTP attempt. Caller owns retry/backoff, not supervision.

        Persist the exact request before I/O. Lost responses replay the same
        sequence/body, including across Worker restarts. No lock spans HTTP.
        """
        if not self._exchange.acquire(blocking=False):
            raise RuntimeError('recovery exchange already in progress')
        try:
            with self._lock:
                r = self.snapshot()
                if r['blocked_reason']:
                    return False
                if r['pending'] is None:
                    at = timestamp(clock())
                    if at < r['last_observed_at']:
                        raise ValueError('monotonic request moved backwards')
                    body = {'generation':r['generation'], 'sequence':r['sequence'],
                        'identity':r['identity'], 'state':r['state'],
                        'elapsed_seconds':at-r['started_at'], 'exit_code':r['exit_code'],
                        'stop_reason':r['stop_reason']}
                    r['pending'] = {'body':body, 'requested_at':at}
                    self._commit(r)
                pending = copy.deepcopy(r['pending'])
                execution = r['execution_id']
            reply = api.post(f'worker/executions/{execution}/recovery/reconcile', pending['body'])
            with self._lock:
                # Watchdog may have observed exit or timeout during the request.
                r = self.snapshot()
                control = self._control()
                def persist(value):
                    candidate = copy.deepcopy(r)
                    candidate['control'] = value
                    self._commit(candidate)
                control.apply(reply, sequence=pending['body']['sequence'],
                    requested_at=pending['requested_at'], received_at=clock(), persist=persist)
                r = self.snapshot()
                r['pending'] = None
                # Completion may proceed only after this exact exit fact was
                # accepted; an ACK for an earlier running sample is not enough.
                r['acknowledged'] = copy.deepcopy(pending['body'])
                r['sequence'] += 1
                self._commit(r)
            return True
        finally:
            self._exchange.release()
