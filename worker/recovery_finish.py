"""Crash-safe completion/cleanup steps owned by one execution Worker thread."""
import copy

from .recovery_runtime import RecoveryRuntime
from .recovery_supervisor import identity
from .storage import atomic_json

TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT')


def finalize(worker, body=None):
    state = worker.state
    if not state or not isinstance(state.get('recovery'), dict):
        raise ValueError('negotiated recovery execution required')
    proof = state['recovery']
    if set(proof) != {'generation', 'identity'} or not isinstance(proof['generation'], str) or not 0 < len(proof['generation']) <= 36:
        raise ValueError('invalid recovery assignment proof')
    identity(proof['identity'])
    execution = state['task']['execution_id']
    runtime = RecoveryRuntime(worker, execution)

    def persist(value):
        # Do not publish a new in-memory phase until its journal is durable.
        atomic_json(worker.path, value)
        worker.state = value

    pending = state.get('recovery_finalization')
    if pending is None:
        if not isinstance(body, dict) or 'recovery' in body:
            raise ValueError('original completion body required')
        observed = runtime.inspect()
        if observed['state'] != 'exited' or observed['identity'] != proof['identity'] or observed['exit_code'] != body.get('exit_code'):
            raise RuntimeError('completion requires original runtime exit evidence')
        pending = {'proof':copy.deepcopy(proof), 'body':copy.deepcopy(body), 'phase':'completion'}
        persist({**state, 'recovery_finalization':pending})
    elif pending['proof'] != proof or (body is not None and pending['body'] != body):
        raise ValueError('recovery completion cannot change across retries')

    def advance(phase):
        nonlocal pending
        pending = {**pending, 'phase':phase}
        persist({**worker.state, 'recovery_finalization':pending})

    def acknowledgement(reply):
        if (not isinstance(reply, dict) or reply.get('status') not in TERMINAL
                or type(reply.get('cleanup_confirmed')) is not bool or type(reply.get('duplicate')) is not bool):
            raise ValueError('invalid recovery completion acknowledgement')

    if pending['phase'] == 'completion':
        reply = worker.api.post(f'worker/executions/{execution}/complete',
                                {**pending['body'], 'recovery':pending['proof']})
        acknowledgement(reply)
        # Even if the server recalls cleanup, inspect/remove only our original
        # runtime before retiring the journal; never trust a reusable name.
        advance('remove')
    if pending['phase'] == 'remove':
        runtime.remove(pending['proof']['identity'])
        advance('cleanup')
    if pending['phase'] == 'cleanup':
        reply = worker.api.post(f'worker/executions/{execution}/cleanup', pending['proof'])
        acknowledgement(reply)
        if not reply['cleanup_confirmed']:
            raise ValueError('cleanup has not been acknowledged')
        advance('done')
    if pending['phase'] != 'done':
        raise ValueError('unknown recovery completion phase')
    if not pending['body'].get('pending_results'):
        persist(None)
    return True


def finish_results(worker, outbox):
    """Retire only after queued business results and their final ACK arrive."""
    state = worker.state
    pending = state.get('recovery_finalization') if state else None
    if not pending or pending['phase'] != 'done' or not pending['body'].get('pending_results'):
        raise ValueError('deferred result journal after cleanup required')
    if pending['proof'] != state['recovery']:
        raise ValueError('result synchronization proof changed')
    if state.get('recovery_results_collected') is not True:
        return False
    if outbox.pending(limit=1):
        return False
    execution = state['task']['execution_id']
    reply = worker.api.post(f'worker/executions/{execution}/recovery/results-finished', pending['proof'])
    if (not isinstance(reply, dict) or reply.get('result_sync') != 'complete'
            or reply.get('cleanup_confirmed') is not True or reply.get('status') not in TERMINAL
            or type(reply.get('duplicate')) is not bool):
        raise ValueError('invalid result synchronization acknowledgement')
    atomic_json(worker.path, None)
    worker.state = None
    return True


def resume_results(worker):
    """One bounded post-cleanup delivery pass, never start or renew a runtime."""
    if worker.state.get('recovery_results_collected') is not True:
        return False
    from .recovery_results import execution_outbox
    from .recovery_delivery import RecoveryDelivery
    outbox = execution_outbox(worker, existing=True)
    RecoveryDelivery(worker.api, outbox, apply_results=True).step()
    return finish_results(worker, outbox)
