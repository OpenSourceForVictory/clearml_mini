"""Cleanup only a durable launch that has never crossed the start boundary."""
from .host_backend import HostBackend
from .recovery_runtime import RecoveryRuntime
from .storage import atomic_json


def acknowledge(api, execution, body):
    reply = api.post(f'worker/executions/{execution}/recovery/not-started', body)
    if (not isinstance(reply, dict) or reply.get('execution_id') != execution
            or reply.get('generation') != body['generation']
            or reply.get('status') not in ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')
            or reply.get('cleanup_confirmed') is not True or type(reply.get('duplicate')) is not bool):
        raise ValueError('invalid not-started acknowledgement')


def settle(worker, launch):
    record = launch.record
    if (record['phase'] != 'blocked' or not record.get('reply')
            or record['reply']['action'] != 'stop' or not record['reply'].get('reason')
            or record['started_at'] is not None or record['identity'] is not None
            or worker.state.get('recovery')):
        raise RuntimeError('launch may have started; original runtime reconciliation required')
    execution = worker.state['task']['execution_id']
    request = {k:v for k,v in {**record['assignment'], 'launch_id':record['launch_id']}.items()
               if k != 'execution_id'}
    body = {'generation':request['generation'], 'request_id':request['request_id'], 'launch':request}
    runtime = RecoveryRuntime(worker, execution)
    # RecoveryLaunch holds exclusive ownership throughout inspection, removal
    # and ACK. Retrying this operation never calls create/start/stop.
    value = runtime._raw()
    if value is not None:
        runtime._marker(value, record['launch_id'])
        if value['State'].get('Status') != 'created' or value['State'].get('Running'):
            raise RuntimeError('runtime may have started; refusing unstarted cleanup')
        if worker.host_mode():
            # No systemd unit exists in this state. Do not issue stop by name.
            (HostBackend(worker).root/'launch.json').unlink(missing_ok=True)
        else:
            from .client import docker
            from .recovery_supervisor import identity
            process = identity({'kind':'docker','boot_id':record['assignment']['boot_id'],
                                'runtime_id':value.get('Id')})
            docker('rm', process['runtime_id'])  # No --force: a racing start fails closed.
        if runtime._raw() is not None:
            raise RuntimeError('unstarted runtime cleanup not confirmed')
    acknowledge(worker.api, execution, body)
    atomic_json(worker.path, None)
    worker.state = None
