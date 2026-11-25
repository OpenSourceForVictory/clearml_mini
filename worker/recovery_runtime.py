"""Runtime observations for the negotiated supervisor, without PID reuse."""
from pathlib import Path
import json
import threading
import uuid

from .host_backend import HostBackend
from .recovery_supervisor import identity
from .storage import atomic_json


class RecoveryRuntime:
    def __init__(self, worker, execution_id):
        if not worker.state or worker.state['task']['execution_id'] != execution_id:
            raise ValueError('runtime does not belong to the execution journal')
        self.worker, self.execution_id = worker, execution_id

    def _raw(self):
        return (HostBackend(self.worker).inspect(self.execution_id, recovery=True)
                if self.worker.host_mode() else self.worker.inspect(self.execution_id))

    def inspect(self):
        observed = self._observation(self._raw())
        if observed['state'] == 'missing':
            return self._saved_exit() or observed
        if observed['state'] == 'exited':
            self._save_exit(observed)
        return observed

    def _exit_path(self):
        return Path(str(self.worker.path)+'.runtime-exit.json')

    def _save_exit(self, observed):
        proof = self.worker.state.get('recovery')
        if not proof or observed['identity'] != proof['identity']:
            return
        lock = self.worker.__dict__.setdefault('_recovery_exit_lock', threading.RLock())
        with lock:
            previous = self._saved_exit()
            if previous is not None:
                if {k:v for k,v in previous.items() if k != 'retained'} != observed:
                    raise ValueError('original runtime exit evidence changed')
                return
            atomic_json(self._exit_path(), {'execution_id':self.execution_id,
                'generation':proof['generation'], 'launch_id':self.worker.state['task'].get('recovery_launch_id'),
                'observation':observed})

    def _saved_exit(self):
        path = self._exit_path()
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise ValueError('invalid runtime exit evidence file')
        saved = json.loads(path.read_text())
        proof = self.worker.state.get('recovery')
        observed = saved['observation']
        if (not proof or saved['execution_id'] != self.execution_id
                or saved['generation'] != proof['generation']
                or saved['launch_id'] != self.worker.state['task'].get('recovery_launch_id')
                or observed['state'] != 'exited' or observed['identity'] != proof['identity']
                or identity(observed['identity'])['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                or (observed['exit_code'] is not None and type(observed['exit_code']) is not int)):
            raise ValueError('runtime exit evidence does not match original execution')
        return {**observed, 'retained':True}

    def _observation(self, value):
        host = self.worker.host_mode()
        # An old exit.json alone does not establish a systemd invocation. Keep
        # previously persisted exit evidence, but do not invent an identity.
        if not value or value['State'].get('Recovered'):
            return {'state':'missing', 'identity':None, 'exit_code':None}
        state = value['State']
        if state.get('Status') == 'created':
            raise RuntimeError('runtime has not started; cannot report a successful exit')
        bound = identity({'kind':'host' if host else 'docker',
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'runtime_id':value.get('InvocationID') if host else value.get('Id')})
        return {'identity':bound, 'state':'running' if state['Running'] else 'exited',
                'exit_code':None if state['Running'] else state.get('ExitCode')}

    def stop(self, expected_identity):
        observed = self.inspect()
        if observed['state'] == 'missing':
            return
        if observed['identity'] != expected_identity:
            raise RuntimeError('refusing to stop a different runtime identity')
        if observed['state'] == 'exited':
            return
        if self.worker.host_mode():
            HostBackend(self.worker).stop(self.execution_id)
        else:
            # Use immutable ID, not the reusable friendly container name.
            from .client import docker
            docker('stop', '--time', '10', expected_identity['runtime_id'], timeout=15)
        after = self._observation(self._raw())
        if after['state'] == 'running' or (after['identity'] is not None and after['identity'] != expected_identity):
            raise RuntimeError('original runtime stop has not been confirmed')
        # A successful stop followed by absence proves termination, but does
        # not supply an exit code. Persist that distinction before any cleanup.
        if after['state'] == 'missing':
            after = {'state':'exited', 'identity':expected_identity, 'exit_code':None}
        self._save_exit(after)

    def remove(self, expected_identity):
        """Only after durable completion; never kill or remove a reused runtime."""
        identity(expected_identity)
        observed = self.inspect()
        if observed['state'] == 'missing':
            return
        if observed['identity'] != expected_identity or observed['state'] != 'exited':
            raise RuntimeError('refusing cleanup of an unconfirmed or different runtime')
        if observed.get('retained'):
            return
        if self.worker.host_mode():
            HostBackend(self.worker).remove(self.execution_id)
        else:
            from .client import docker
            docker('rm', expected_identity['runtime_id'])

    def _marker(self, value, expected):
        if str(uuid.UUID(expected)) != expected or self.worker.state['task'].get('recovery_launch_id') != expected:
            raise ValueError('runtime launch marker does not match original task')
        marker = (value.get('LaunchID') if self.worker.host_mode()
                  else value.get('Config',{}).get('Labels',{}).get('mini-eval.launch')) if value else None
        if marker != expected:
            raise RuntimeError('runtime launch marker missing or mismatched')
        return marker

    def inspect_launch(self, expected):
        value = self._raw()
        if not value or value['State'].get('Recovered'):
            observed = self._saved_exit()
            if observed is not None:
                if str(uuid.UUID(expected)) != expected or self.worker.state['task'].get('recovery_launch_id') != expected:
                    raise ValueError('runtime launch marker does not match original task')
                return {'launch_id':expected, 'identity':observed['identity']}
        marker = self._marker(value, expected)
        observed = self._observation(value)
        if observed['identity'] is None:
            raise RuntimeError('runtime identity unavailable; reconciliation required')
        return {'launch_id':marker, 'identity':observed['identity']}

    def start(self, expected):
        """Used only inside RecoveryLaunch.start after its durable attempt write."""
        value = self._raw()
        self._marker(value, expected)
        if value['State'].get('Status') != 'created':
            raise RuntimeError('runtime already started; reconcile instead')
        if self.worker.host_mode():
            HostBackend(self.worker).start(self.worker.state['task'])
        else:
            from .client import docker
            # Created container identity is already immutable; never start by name.
            bound = identity({'kind':'docker', 'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                              'runtime_id':value.get('Id')})
            docker('start', bound['runtime_id'], timeout=10)
        return self.inspect_launch(expected)
