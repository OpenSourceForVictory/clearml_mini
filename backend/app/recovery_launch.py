"""Prelaunch confirmation for an original claim; never allocate or restart."""
from datetime import datetime, timedelta
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from typing import Literal

from .core import ExecutionRecovery, ExecutionRecoveryClaim, ExecutionRecoveryControl, Run, Slot, now
from .recovery_control import platform_stop_reason
from .scheduling import fast_lock, utc

# Short-lived permission to start, distinct from the lifetime of an offline run.
LAUNCH_WINDOW_SECONDS = 30


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=36)
    request_id: uuid.UUID
    launch_id: uuid.UUID
    boot_id: str = Field(min_length=1, max_length=128)
    kind: Literal['host', 'docker']


def prepare(s, *, worker_id, execution_id, request):
    grant = s.get(ExecutionRecovery, execution_id)
    if not grant or grant.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if grant.generation != request.generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    fast_lock(s)
    run = s.scalar(select(Run).where(Run.id == grant.run_id).with_for_update()
                   .execution_options(populate_existing=True))
    grant = s.scalar(select(ExecutionRecovery).where(ExecutionRecovery.execution_id == execution_id)
                     .with_for_update().execution_options(populate_existing=True))
    claim = s.get(ExecutionRecoveryClaim, str(request.request_id))
    slot = s.get(Slot, grant.slot_id)
    if not claim or claim.execution_id != execution_id or not slot or slot.worker_id != worker_id:
        raise HTTPException(404, 'original claim not found')
    control = s.get(ExecutionRecoveryControl, execution_id)
    snapshot = dict(control.snapshot) if control else {}
    previous = snapshot.get('launch')
    data = request.model_dump(mode='json')
    if previous and previous['request'] != data:
        raise HTTPException(409, 'launch identity is immutable')
    at = now()
    issued = datetime.fromisoformat(previous['issued_at']) if previous else at
    window = previous['window_seconds'] if previous else LAUNCH_WINDOW_SECONDS
    owns = bool(run and run.execution_id == execution_id and run.slot_id == grant.slot_id)
    reason = platform_stop_reason(run, grant) if owns else None
    action = 'launch'
    if not owns or snapshot.get('identity_conflict'):
        action, reason = 'inspect', 'process_or_assignment_identity_changed'
    elif reason or snapshot.get('stop_requested'):
        action, reason = 'stop', reason or snapshot['stop_reason']
    elif snapshot.get('identity') or run.status != 'STARTING':
        action, reason = 'inspect', 'runtime_must_be_reconciled'
    elif previous and previous['action'] != 'launch':
        action, reason = previous['action'], previous['reason']
    else:
        tolerance = grant.policy['disconnect_tolerance_seconds']
        origin = utc(run.enqueued_at)
        if tolerance is not None and origin is None:
            action, reason = 'inspect', 'missing_original_control_time'
        elif tolerance is not None and (at-origin).total_seconds() >= tolerance:
            action, reason = 'stop', 'disconnection_timeout'
        elif at >= issued + timedelta(seconds=window):
            action, reason = 'inspect', 'launch_confirmation_expired'
        elif tolerance is not None and not previous:
            # Bound the first launch by the original offline cutoff as well.
            # The node measures this from request send, conservatively before
            # server receipt, and duplicates cannot extend either window.
            window = min(window, tolerance-(at-origin).total_seconds())
    snapshot['launch'] = {'request':data, 'issued_at':issued.isoformat(), 'window_seconds':window, 'action':action, 'reason':reason}
    if action == 'stop':
        snapshot.update(stop_requested=True, stop_reason=reason)
    if control:
        control.snapshot = snapshot
    else:
        s.add(ExecutionRecoveryControl(execution_id=execution_id, snapshot=snapshot))
    s.flush()
    return {'execution_id':execution_id, **data, 'action':action, 'reason':reason,
            'duplicate':previous is not None, 'valid_for_seconds':window}


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/recovery/prepare')
    def prepare_launch(execution_id: str, body: LaunchRequest, worker=Depends(worker_auth), s=Depends(db)):
        reply = prepare(s, worker_id=worker.id, execution_id=execution_id, request=body)
        s.commit()
        return reply
