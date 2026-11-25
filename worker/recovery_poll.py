"""Persist negotiated assignments before dispatch; never launch from a receipt."""
import copy
import json
from pathlib import Path
import time

from .recovery_claim import RecoveryClaim
from .storage import atomic_json


CAPABILITY = 'disconnected_execution_v1'


def poll(worker, software):
    path = Path(str(worker.path)+'.claim.json')
    pending = json.loads(path.read_text()) if path.exists() else None
    negotiated = CAPABILITY in software.get('capabilities', [])
    if negotiated and pending is None:
        root = worker.config.get('node_outbox_root')
        if (not isinstance(root, str) or not root or not Path(root).is_absolute()
                or Path(root).is_symlink()):
            raise ValueError('negotiated recovery requires a shared node outbox root')
    endpoint = f'worker/slots/{worker.slot_id}/claim'
    if not negotiated and pending is None:
        response = worker.api.post(endpoint, {'known_execution_ids':list(worker.children)})
        if response.get('recovery_claim') is not None:
            raise RuntimeError('unexpected recovery assignment without durable claim request')
        return response

    journal = RecoveryClaim(path)
    try:
        request = journal.begin(slot_id=worker.slot_id, known_execution_ids=list(worker.children),
            requested_at=time.monotonic(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        response = worker.api.post(endpoint, {**request, 'recovery_version':1})
        if (not isinstance(response, dict) or response.get('request_id') != request['request_id']
                or 'recovery_claim' not in response or 'task' not in response
                or not isinstance(response.get('cleanup'), list)):
            raise ValueError('invalid durable claim acknowledgement')
        receipt = response['recovery_claim']
        if receipt is None:
            if response['task'] is not None:
                raise ValueError('assignment missing durable recovery receipt')
            journal.empty(request['request_id'])
            return response
        if not isinstance(receipt, dict) or response['cleanup'] or response['task'] != receipt.get('task'):
            raise ValueError('claim response conflicts with recovery receipt')
        journal.accept(receipt)
        if receipt['disposition'] == 'stop':
            child = worker.child(receipt['execution_id'])
            if child.state is not None:
                old = child.state.get('recovery_assignment', {}).get('claim', {}).get('receipt', {})
                if any(old.get(k) != receipt[k] for k in ('request_id','execution_id','generation','digest')):
                    raise ValueError('stopped claim conflicts with existing execution journal')
                # Lost handoff ACK: retain the original launch/monitor journal.
                journal.handoff(lambda assignment: None)
                worker.children[receipt['execution_id']] = child
                return {**response, 'task':child.state['task']}
            if Path(str(child.path)+'.launch.json').exists():
                raise RuntimeError('launch journal exists without original execution journal')
            from .recovery_unstarted import acknowledge
            acknowledge(worker.api, receipt['execution_id'], {
                'generation':receipt['generation'], 'request_id':receipt['request_id'], 'launch':None})
            journal.handoff(lambda assignment: None)
            return response
        if receipt['disposition'] != 'reconcile':
            # A stopped, never-started assignment needs its own control
            # reconciliation. Retain the receipt; never invent a process identity
            # or send legacy cleanup just to permit another allocation.
            raise RuntimeError('recovery claim requires stop reconciliation')

        task = receipt['task']
        child = worker.child(task['execution_id'])

        def persist(assignment):
            if child.state is not None:
                old = child.state.get('recovery_assignment')
                if old != assignment:
                    raise ValueError('existing execution journal conflicts with claim assignment')
                # A crash after child commit and before claim retirement must
                # preserve all subsequent progress, offsets and runtime identity.
                return
            state = {'service_started_at':time.time(),
                'host_approval':copy.deepcopy(child.config.get('_host_approval', {})),
                'runtime':copy.deepcopy(child.runtime_snapshot),
                'storage':copy.deepcopy(child.storage_snapshot), 'task':copy.deepcopy(task),
                'offset':0, 'sequence':0, 'created':False, 'pending':None,
                'recovery_assignment':copy.deepcopy(assignment)}
            atomic_json(child.path, state)
            child.state = state

        journal.handoff(persist)
        worker.children[task['execution_id']] = child
        return response
    finally:
        journal.close()
