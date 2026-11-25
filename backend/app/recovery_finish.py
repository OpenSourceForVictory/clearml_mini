"""Identity-bound completion and separate cleanup for negotiated executions."""
from fastapi import HTTPException
from sqlalchemy import select, func

from .core import ExecutionRecovery, ExecutionRecoveryControl, Run, Slot, RunLogState
from .scheduling import TERMINAL, fast_lock, transition


def locked(s, worker_id, execution_id, proof, *, resource_lock=True):
    grant = s.get(ExecutionRecovery, execution_id)
    if not grant or grant.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if grant.generation != proof.generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    if resource_lock:
        fast_lock(s)
    run = s.scalar(select(Run).where(Run.id == grant.run_id).with_for_update()
                   .execution_options(populate_existing=True))
    grant = s.scalar(select(ExecutionRecovery).where(ExecutionRecovery.execution_id == execution_id)
                     .with_for_update().execution_options(populate_existing=True))
    slot = s.get(Slot, grant.slot_id)
    if not run or run.execution_id != execution_id or run.slot_id != grant.slot_id or not slot or slot.worker_id != worker_id:
        raise HTTPException(409, 'recovery execution ownership changed')
    control = s.get(ExecutionRecoveryControl, execution_id)
    snapshot = control.snapshot if control else {}
    if snapshot.get('identity_conflict') or snapshot.get('identity') != proof.identity.model_dump():
        raise HTTPException(409, 'original process identity has not been reconciled')
    return run, grant, control


def finish(s, *, worker_id, execution_id, body):
    run, grant, control = locked(s, worker_id, execution_id, body.recovery)
    snapshot = control.snapshot
    fact = body.model_dump(mode='json', exclude={'recovery'})
    if not fact['pending_results']:
        fact.pop('pending_results')  # Preserve receipts written before this field existed.
    previous = snapshot.get('completion')
    if previous is not None:
        if previous != fact:
            raise HTTPException(409, 'conflicting recovery completion')
        return {'status':run.status, 'cleanup_confirmed':run.cleanup_confirmed, 'duplicate':True}
    if snapshot.get('observed_state') != 'exited' or snapshot.get('exit_code') != body.exit_code:
        raise HTTPException(409, 'completion requires reconciled process exit')
    if run.status not in (*TERMINAL, 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'):
        raise HTTPException(409, 'execution was not claimed')
    from .execution_logs import terminal_log_lock
    terminal_log_lock(s, run.id)
    # Platform cancellation/timeout/terminal state wins over late business facts.
    if run.status not in TERMINAL:
        if run.status == 'CANCEL_REQUESTED' or grant.revoked:
            status = 'CANCELLED'
        elif snapshot.get('stop_reason') in ('execution_timeout', 'disconnection_timeout'):
            status = 'TIMED_OUT'
        else:
            from .execution import validate_finish
            validate_finish(s, run, body, require_results=not body.pending_results)
            status = body.status
        if status == 'SUCCEEDED' and body.metrics:
            run.result_json = body.metrics.model_dump()
        transition(s, run, status, body.error)
    run.exit_code = body.exit_code
    if body.log_gap:
        logs = s.get(RunLogState, run.id)
        if logs is None:
            logs = RunLogState(run_id=run.id)
            s.add(logs)
        logs.truncated = True
    control.snapshot = {**snapshot, 'completion':fact,
                        'result_sync':'pending' if body.pending_results else 'complete'}
    s.flush()
    # Completion alone does not authorize resource reuse.
    return {'status':run.status, 'cleanup_confirmed':run.cleanup_confirmed, 'duplicate':False}


def cleanup(s, *, worker_id, execution_id, proof):
    run, grant, control = locked(s, worker_id, execution_id, proof)
    snapshot = control.snapshot
    if 'completion' not in snapshot or run.status not in TERMINAL:
        raise HTTPException(409, 'recovery cleanup requires acknowledged completion')
    duplicate = snapshot.get('cleanup_confirmed', False)
    if not duplicate:
        # An authenticated node sends this only after removing the bound runtime.
        run.cleanup_confirmed = True
        control.snapshot = {**snapshot, 'cleanup_confirmed':True}
        s.flush()
        others = s.scalar(select(func.count()).select_from(Run).where(
            Run.slot_id == run.slot_id, Run.cleanup_confirmed.is_(False)))
        if not others:
            s.get(Slot, run.slot_id).quarantined = False
        from . import memberships
        memberships.reconcile(s)
    return {'status':run.status, 'cleanup_confirmed':True, 'duplicate':duplicate}


def results_finished(s, *, worker_id, execution_id, proof):
    """Confirm late business data independently of already released resources."""
    # Sync acknowledgement changes no lifecycle/allocation. Match result
    # application's Run -> grant order without taking the scheduler lock.
    run, grant, control = locked(s, worker_id, execution_id, proof, resource_lock=False)
    snapshot = control.snapshot
    if not snapshot.get('completion') or run.status not in TERMINAL or not run.cleanup_confirmed:
        raise HTTPException(409, 'result synchronization requires acknowledged completion and cleanup')
    duplicate = snapshot.get('result_sync') == 'complete'
    if not duplicate:
        from .execution import Finish, validate_finish
        body = Finish.model_validate(snapshot['completion'])
        validate_finish(s, run, body)
        control.snapshot = {**snapshot, 'result_sync':'complete'}
        s.flush()
    return {'status':run.status, 'cleanup_confirmed':run.cleanup_confirmed,
            'result_sync':'complete', 'duplicate':duplicate}
