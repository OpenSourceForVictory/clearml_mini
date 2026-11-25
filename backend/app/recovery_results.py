"""Apply reliable business facts without renewing or reopening an execution."""
from fastapi import HTTPException
from sqlalchemy import select

from .core import ExecutionRecovery, ExecutionRecoveryReceipt, Job, Run, Slot
from .recovery_records import batch_deleted, receive


def apply(s, *, worker_id, execution_id, generation, kind, sequence, body):
    # Stable order shared with lifecycle transitions: Run then grant. No global
    # scheduler lock is needed: this operation never changes resource ownership.
    authority = s.get(ExecutionRecovery, execution_id)
    if not authority or authority.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if authority.generation != generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    run = s.scalar(select(Run).where(Run.id == authority.run_id).with_for_update()
                   .execution_options(populate_existing=True))
    authority = s.scalar(select(ExecutionRecovery).where(
        ExecutionRecovery.execution_id == execution_id).with_for_update()
        .execution_options(populate_existing=True))
    if authority.revoked or not run:
        raise HTTPException(410, 'recovery business data is no longer accepted')
    slot = s.get(Slot, authority.slot_id)
    if (run.execution_id != execution_id or run.slot_id != authority.slot_id
            or not slot or slot.worker_id != worker_id):
        raise HTTPException(409, 'recovery execution ownership changed')
    job = s.get(Job, run.job_id)
    if not job:
        raise HTTPException(410, 'recovery business data is no longer accepted')
    # The receipt keeps the original payload even when business validation
    # rejects it; only successful application or durable rejection may be ACKed.
    deleted = batch_deleted(s, job)
    reply = receive(s, worker_id=worker_id, execution_id=execution_id,
                    generation=generation, kind=kind, sequence=sequence, body=body,
                    allow_deleted=deleted)
    receipt = s.get(ExecutionRecoveryReceipt, (execution_id, kind, sequence))
    if deleted:
        # Explicitly acknowledge dropping original business data only after
        # the deleted-batch tombstone and its immutable digest are committed.
        # A retry of a previously applied receipt keeps its original history.
        if receipt.application_state is None:
            receipt.application_state = 'discarded'
            s.flush()
        return {**reply, 'reconciled': True, 'application': 'discarded'}
    if receipt.application_state == 'rejected':
        return {**reply, 'reconciled': True, 'application': 'rejected',
                'reason': receipt.rejection_reason}
    if receipt.application_state == 'applied':
        return {**reply, 'reconciled': True, 'application': 'applied'}
    try:
        # A bad business event must roll back its partial writes but retain
        # the immutable original receipt and digest. Only business validation
        # 400 is final; ownership, sequencing conflicts and capacity still fail.
        with s.begin_nested():
            if kind == 'episode':
                from .episodes import record
                record(s, run, body)
            elif kind == 'task_result':
                from runner.task_result_contract import validate
                template = (job.client_spec or {}).get('template', {})
                if template.get('integration_mode') != 'sdk-task-result' or template.get('task_type') != 'command':
                    raise HTTPException(400, 'execution does not use task-result SDK reporting')
                try:
                    result = {'kind': 'task', **validate(body)}
                except (ValueError, TypeError, OverflowError) as exc:
                    raise HTTPException(400, str(exc)) from None
                if run.result_json is not None and run.result_json != result:
                    raise HTTPException(409, 'conflicting task result for execution')
                run.result_json = result
            else:
                raise HTTPException(422, 'unsupported recovery business result')
            s.flush()
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        receipt.application_state = 'rejected'
        receipt.rejection_reason = str(exc.detail)[:1000]
        s.flush()
        return {**reply, 'reconciled': True, 'application': 'rejected',
                'reason': receipt.rejection_reason}
    receipt.application_state = 'applied'
    s.flush()
    return {**reply, 'reconciled': True, 'application': 'applied'}
