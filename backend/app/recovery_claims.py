"""Original claim receipts; recovering one never grants permission to launch."""
import hashlib
import json
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .core import ExecutionRecovery, ExecutionRecoveryClaim, Run, Slot
from .recovery_records import grant
from .scheduling import TERMINAL, fast_lock


class Lookup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID


def key(value):
    try:
        canonical = str(uuid.UUID(value))
        if canonical != value:
            raise ValueError()
        return canonical
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, 'canonical claim request ID required') from None


def bind(s, *, request_id, worker_id, slot_id, run_id, task, tolerance=None):
    """Within the claim transaction, before its response is committed.

    No production caller until launch/result/cleanup negotiation is complete.
    Caller must roll back on any error; grant and snapshot commit together.
    """
    request_id = key(request_id)
    if not isinstance(task, dict):
        raise HTTPException(422, 'claim task must be an object')
    try:
        encoded = json.dumps(task, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, 'invalid claim task encoding') from None
    if len(encoded) > 1024*1024:
        raise HTTPException(413, 'claim task snapshot exceeds limit')
    digest = hashlib.sha256(encoded).hexdigest()
    # Allocation transitions already use this lock order. It also serializes
    # concurrent retries before the unique request/execution constraints.
    fast_lock(s)
    run = s.scalar(select(Run).where(Run.id == run_id).with_for_update()
                   .execution_options(populate_existing=True))
    slot = s.get(Slot, slot_id)
    if not run or not slot or slot.worker_id != worker_id or run.slot_id != slot_id:
        raise HTTPException(404, 'claim execution not found')
    if (task.get('execution_id') != run.execution_id or task.get('run_id') != run.id
            or task.get('job_id') != run.job_id):
        raise HTTPException(409, 'claim task does not match assignment')
    previous = s.get(ExecutionRecoveryClaim, request_id)
    other = s.scalar(select(ExecutionRecoveryClaim).where(ExecutionRecoveryClaim.execution_id == run.execution_id))
    if previous and (previous.execution_id != run.execution_id or previous.digest != digest):
        raise HTTPException(409, 'claim request was already bound to different content')
    if other and other.request_id != request_id:
        raise HTTPException(409, 'execution already belongs to another claim request')
    authority = grant(s, run, worker_id, tolerance)
    if not previous:
        s.add(ExecutionRecoveryClaim(request_id=request_id, execution_id=authority.execution_id,
                                    digest=digest, task_snapshot=json.loads(encoded)))
        s.flush()
    return lookup(s, worker_id=worker_id, slot_id=slot_id, request_id=request_id)


def lookup(s, *, worker_id, slot_id, request_id):
    request_id = key(request_id)
    receipt = s.get(ExecutionRecoveryClaim, request_id)
    authority = s.get(ExecutionRecovery, receipt.execution_id) if receipt else None
    slot = s.get(Slot, slot_id)
    if (not authority or authority.worker_id != worker_id or authority.slot_id != slot_id
            or not slot or slot.worker_id != worker_id):
        raise HTTPException(404, 'claim receipt not found')
    run = s.get(Run, authority.run_id)
    disposition = 'reconcile'
    if authority.revoked:
        disposition = 'revoked'
    elif not run:
        disposition = 'deleted'
    elif run.execution_id != authority.execution_id or run.slot_id != slot_id:
        disposition = 'conflict'
    elif run.status in TERMINAL or run.status == 'CANCEL_REQUESTED':
        disposition = 'stop'
    return {'request_id':request_id, 'execution_id':authority.execution_id,
            'generation':authority.generation, 'policy':authority.policy,
            'digest':receipt.digest, 'launch_authorized':False, 'disposition':disposition,
            'task':receipt.task_snapshot if disposition == 'reconcile' else None}


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/slots/{slot_id}/recovery/claim')
    def recover_claim(slot_id: str, body: Lookup, worker=Depends(worker_auth), s=Depends(db)):
        return lookup(s, worker_id=worker.id, slot_id=slot_id, request_id=str(body.request_id))
