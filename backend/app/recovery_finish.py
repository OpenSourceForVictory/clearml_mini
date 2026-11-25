"""Identity-bound completion and separate cleanup for negotiated executions."""
from fastapi import HTTPException
from sqlalchemy import select, func

from .core import ExecutionRecovery, ExecutionRecoveryControl, ExecutionRecoveryReceipt, Run, Slot, RunLogState
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
    control.snapshot = {**snapshot, 'completion':fact, 'platform_status':run.status,
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
    # The visible Run/Job may have been purged after this seal committed while
    # its HTTP response was lost. Keep the original authority/control ledger
    # so the same Worker can recover that final ACK without reviving the batch.
    authority = s.get(ExecutionRecovery, execution_id)
    if authority and authority.worker_id == worker_id and authority.generation == proof.generation:
        prior = s.get(ExecutionRecoveryControl, execution_id)
        if (prior and prior.snapshot.get('identity') == proof.identity.model_dump()
                and prior.snapshot.get('cleanup_confirmed') is True
                and prior.snapshot.get('result_sync') in ('complete', 'failed')
                and prior.snapshot.get('platform_status') in TERMINAL
                and s.get(Run, authority.run_id) is None):
            slot = s.get(Slot, authority.slot_id)
            if not slot or slot.worker_id != worker_id:
                raise HTTPException(409, 'recovery execution ownership changed')
            return {'status':prior.snapshot['platform_status'], 'cleanup_confirmed':True,
                    'result_sync':prior.snapshot['result_sync'], 'duplicate':True}
    # Sync acknowledgement changes no lifecycle/allocation. Match result
    # application's Run -> grant order without taking the scheduler lock.
    run, grant, control = locked(s, worker_id, execution_id, proof, resource_lock=False)
    snapshot = control.snapshot
    if not snapshot.get('completion') or run.status not in TERMINAL or not run.cleanup_confirmed:
        raise HTTPException(409, 'result synchronization requires acknowledged completion and cleanup')
    duplicate = snapshot.get('result_sync') in ('complete', 'failed')
    if not duplicate:
        rejection = s.scalar(select(ExecutionRecoveryReceipt).where(
            ExecutionRecoveryReceipt.execution_id == execution_id,
            ExecutionRecoveryReceipt.application_state == 'rejected')
            .order_by(ExecutionRecoveryReceipt.received_at,
                      ExecutionRecoveryReceipt.sequence).limit(1))
        if rejection:
            control.snapshot = {**snapshot, 'result_sync':'failed',
                'result_sync_error':f'{rejection.kind} #{rejection.sequence}: {rejection.rejection_reason}'}
        else:
            from .execution import Finish, validate_finish
            body = Finish.model_validate(snapshot['completion'])
            if run.status in ('CANCELLED', 'TIMED_OUT'):
                # Platform control won over a late business-success summary.
                # A stopped service/client need not produce a complete protocol
                # result, but its durable queue still needs an explicit seal.
                control.snapshot = {**snapshot, 'result_sync':'complete'}
            else:
                try:
                    # results-finished is the Worker's final seal after it has
                    # drained its durable queue. Missing results are then a
                    # terminal protocol failure, not a reason to retry forever.
                    validate_finish(s, run, body)
                except HTTPException as exc:
                    if exc.status_code != 400:
                        raise
                    control.snapshot = {**snapshot, 'result_sync':'failed',
                        'result_sync_error':str(exc.detail)[:1000]}
                else:
                    control.snapshot = {**snapshot, 'result_sync':'complete'}
        s.flush()
    return {'status':run.status, 'cleanup_confirmed':run.cleanup_confirmed,
            'result_sync':control.snapshot['result_sync'], 'duplicate':duplicate}
