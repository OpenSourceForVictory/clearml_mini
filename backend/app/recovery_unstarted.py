"""Release an original stopped assignment only after proven non-start cleanup."""
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func

from .core import ExecutionRecovery, ExecutionRecoveryClaim, ExecutionRecoveryControl, Run, Slot
from .recovery_launch import LaunchRequest
from .recovery_control import platform_stop_reason
from .scheduling import fast_lock, transition, TERMINAL


class NotStarted(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=36)
    request_id: uuid.UUID
    launch: LaunchRequest | None = None


def settle(s, *, worker_id, execution_id, body):
    grant = s.get(ExecutionRecovery, execution_id)
    if not grant or grant.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if grant.generation != body.generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    fast_lock(s)
    run = s.scalar(select(Run).where(Run.id == grant.run_id).with_for_update()
                   .execution_options(populate_existing=True))
    grant = s.scalar(select(ExecutionRecovery).where(ExecutionRecovery.execution_id == execution_id)
                     .with_for_update().execution_options(populate_existing=True))
    slot = s.get(Slot, grant.slot_id)
    claim = s.get(ExecutionRecoveryClaim, str(body.request_id))
    if (not run or run.execution_id != execution_id or run.slot_id != grant.slot_id
            or not slot or slot.worker_id != worker_id or not claim or claim.execution_id != execution_id):
        raise HTTPException(409, 'original claim ownership changed')
    control = s.get(ExecutionRecoveryControl, execution_id)
    snapshot = dict(control.snapshot) if control else {}
    fact = body.model_dump(mode='json')
    previous = snapshot.get('not_started')
    if previous is not None:
        if previous != fact:
            raise HTTPException(409, 'conflicting not-started acknowledgement')
        return {'execution_id':execution_id, 'generation':grant.generation,
                'status':run.status, 'cleanup_confirmed':True, 'duplicate':True}
    if (snapshot.get('identity') or snapshot.get('identity_conflict') or snapshot.get('completion')
            or run.started_at is not None or run.status == 'RUNNING'):
        raise HTTPException(409, 'runtime must be reconciled, not declared unstarted')
    launch = snapshot.get('launch')
    if launch:
        if (body.launch is None or launch['request'] != body.launch.model_dump(mode='json')
                or launch['action'] != 'stop'):
            raise HTTPException(409, 'original stopped launch confirmation required')
    elif body.launch is not None:
        raise HTTPException(409, 'launch confirmation not found')
    reason = platform_stop_reason(run, grant)
    if not reason and snapshot.get('stop_requested'):
        reason = snapshot.get('stop_reason')
    if not reason:
        raise HTTPException(409, 'execution is still authorized')
    from .execution_logs import terminal_log_lock
    terminal_log_lock(s, run.id)
    if run.status not in TERMINAL:
        status = 'TIMED_OUT' if reason in ('execution_timeout','disconnection_timeout') else 'CANCELLED'
        transition(s, run, status, 'execution stopped before business process launch')
    run.cleanup_confirmed = True
    value = {**snapshot, 'not_started':fact, 'cleanup_confirmed':True, 'result_sync':'complete'}
    if control:
        control.snapshot = value
    else:
        s.add(ExecutionRecoveryControl(execution_id=execution_id, snapshot=value))
    s.flush()
    others = s.scalar(select(func.count()).select_from(Run).where(
        Run.slot_id == run.slot_id, Run.cleanup_confirmed.is_(False)))
    if not others:
        slot.quarantined = False
    from . import memberships
    memberships.reconcile(s)
    return {'execution_id':execution_id, 'generation':grant.generation,
            'status':run.status, 'cleanup_confirmed':True, 'duplicate':False}


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/recovery/not-started')
    def not_started(execution_id: str, body: NotStarted, worker=Depends(worker_auth), s=Depends(db)):
        reply = settle(s, worker_id=worker.id, execution_id=execution_id, body=body)
        s.commit()
        return reply
