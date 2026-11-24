"""Runtime observations for the negotiated supervisor, without PID reuse."""
from pathlib import Path

from .host_backend import HostBackend
from .recovery_supervisor import identity


class RecoveryRuntime:
    def __init__(self, worker, execution_id):
        if not worker.state or worker.state['task']['execution_id'] != execution_id:
            raise ValueError('runtime does not belong to the execution journal')
        self.worker, self.execution_id = worker, execution_id

    def inspect(self):
        host = self.worker.host_mode()
        value = (HostBackend(self.worker).inspect(self.execution_id, recovery=True)
                 if host else self.worker.inspect(self.execution_id))
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
