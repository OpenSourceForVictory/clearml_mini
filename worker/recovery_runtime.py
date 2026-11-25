"""Runtime observations for the negotiated supervisor, without PID reuse."""
from pathlib import Path
import uuid

from .host_backend import HostBackend
from .recovery_supervisor import identity


class RecoveryRuntime:
    def __init__(self, worker, execution_id):
        if not worker.state or worker.state['task']['execution_id'] != execution_id:
            raise ValueError('runtime does not belong to the execution journal')
        self.worker, self.execution_id = worker, execution_id

    def _raw(self):
        return (HostBackend(self.worker).inspect(self.execution_id, recovery=True)
                if self.worker.host_mode() else self.worker.inspect(self.execution_id))

    def inspect(self):
        return self._observation(self._raw())

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

    def remove(self, expected_identity):
        """Only after durable completion; never kill or remove a reused runtime."""
        identity(expected_identity)
        observed = self.inspect()
        if observed['state'] == 'missing':
            return
        if observed['identity'] != expected_identity or observed['state'] != 'exited':
            raise RuntimeError('refusing cleanup of an unconfirmed or different runtime')
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
