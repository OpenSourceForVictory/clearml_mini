"""Join a durable claim to a single runtime launch, without legacy leases."""
import copy
from pathlib import Path
import time

from .recovery_launch import RecoveryLaunch
from .recovery_runtime import RecoveryRuntime
from .storage import atomic_json


def start_or_recover(worker, *, clock=time.monotonic):
    state = worker.state
    assignment = state.get('recovery_assignment') if state else None
    if not assignment or assignment.get('reconciliation_required') is not True:
        raise ValueError('durable recovery assignment required')
    claim = assignment['claim']
    receipt = claim['receipt']
    task = state['task']
    original = {k:v for k,v in task.items() if k != 'recovery_launch_id'}
    if (receipt['disposition'] != 'reconcile' or receipt['task'] != original
            or receipt['execution_id'] != task['execution_id']
            or claim['slot_id'] != worker.slot_id):
        raise ValueError('launch does not match original claim')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if boot != claim['boot_id']:
        raise RuntimeError('claim belongs to previous boot; reconciliation required')
    launch = RecoveryLaunch(Path(str(worker.path)+'.launch.json'),
        execution_id=task['execution_id'], generation=receipt['generation'],
        request_id=receipt['request_id'], boot_id=boot,
        kind='host' if worker.host_mode() else 'docker')

    def persist(value):
        atomic_json(worker.path, value)
        worker.state = value

    try:
        marker = launch.record['launch_id']
        if task.get('recovery_launch_id') not in (None, marker):
            raise ValueError('execution journal launch marker changed')
        if task.get('recovery_launch_id') is None:
            persist({**worker.state, 'task':{**task, 'recovery_launch_id':marker}})
        runtime = RecoveryRuntime(worker, task['execution_id'])
        phase = launch.record['phase']
        if worker.state.get('recovery') and phase not in ('attempted', 'bound'):
            raise ValueError('runtime proof exists without a durable launch attempt')
        if phase == 'blocked':
            if (launch.record.get('reply') or {}).get('action') == 'stop' and launch.record['reply'].get('reason'):
                from .recovery_unstarted import settle
                return settle(worker, launch)
            raise RuntimeError('launch blocked; control reconciliation required')
        if phase in ('attempted','bound'):
            # The runtime call may have succeeded even when its response or
            # parent journal write failed. Inspect its marker; never call start.
            bound = launch.bind(runtime.inspect_launch(marker))
        else:
            with worker.start_lock:
                value = runtime._raw()
                if value is None:
                    if worker.state['created']:
                        raise RuntimeError('creation outcome unknown; refusing duplicate execution')
                    persist({**worker.state, 'created':True})
                    worker.create(worker.state['task'])
                # Ensure an existing object is our still-unstarted runtime.
                value = runtime._raw()
                runtime._marker(value, marker)
                if value['State']['Status'] != 'created':
                    raise RuntimeError('runtime already started without durable launch attempt')
            # Runtime preparation can take minutes. Request permission only
            # afterwards, with no cross-execution start lock held during HTTP.
            request = launch.request(clock())
            reply = worker.api.post(f"worker/executions/{task['execution_id']}/recovery/prepare", request)
            if not launch.accept(reply, clock()):
                if reply['action'] == 'stop' and reply.get('reason'):
                    from .recovery_unstarted import settle
                    return settle(worker, launch)
                raise RuntimeError('launch not authorized; control reconciliation required')
            with worker.start_lock:
                bound = launch.start(clock(), runtime.start)
        proof = {'generation':receipt['generation'], 'identity':bound['identity']}
        if worker.state.get('recovery') not in (None, proof):
            raise ValueError('original runtime proof changed')
        persist({**worker.state, 'recovery':copy.deepcopy(proof)})
        return copy.deepcopy(bound)
    finally:
        launch.close()
