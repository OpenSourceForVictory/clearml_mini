"""Transactional receipt ledger for negotiated disconnected executions.

Receipt means durably received, not reconciled, successfully evaluated or safe
to release. No lease or scheduling transition is performed by this module.
Callers ACK only after committing the encompassing transaction.
"""
import hashlib
import json
import uuid

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import select

from .core import (EvaluationBatch, ExecutionRecovery, ExecutionRecoveryReceipt,
                   Job, Run, ServiceInstance, Slot)

MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 25000
CONTROL_BYTES = 65536
CONTROL_RECORDS = 16
MAX_RECORD_BYTES = 65536


class ControlRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=36)
    sequence: int = Field(strict=True, ge=0, lt=2**63)
    body: dict


class ResultRecord(ControlRecord):
    kind: Literal['episode', 'task_result']
    apply: Literal[True] | None = None


def grant(s, run, worker_id, tolerance=None):
    """Called only in the allocation transaction after both peers negotiate."""
    if tolerance is not None and (type(tolerance) is not int or not 0 < tolerance < 2**63):
        raise HTTPException(422, 'invalid disconnection tolerance')
    slot = s.get(Slot, run.slot_id)
    if not run.execution_id or not slot or slot.worker_id != worker_id:
        raise HTTPException(409, 'execution ownership is not established')
    policy = {'version': 1, 'disconnect_tolerance_seconds': tolerance}
    existing = s.get(ExecutionRecovery, run.execution_id)
    if existing:
        if (existing.run_id, existing.worker_id, existing.slot_id, existing.policy) != (
                run.id, worker_id, slot.id, policy):
            raise HTTPException(409, 'recovery assignment is immutable')
        if existing.revoked:
            raise HTTPException(409, 'recovery assignment was revoked')
        return existing
    if run.status != 'STARTING' or run.cleanup_confirmed:
        raise HTTPException(409, 'recovery grant requires a newly claimed execution')
    record = ExecutionRecovery(execution_id=run.execution_id, run_id=run.id,
        worker_id=worker_id, slot_id=slot.id, generation=str(uuid.uuid4()), policy=policy)
    s.add(record)
    s.flush()
    return record


def batch_deleted(s, job):
    batch_id = job.batch_id
    if job.service_instance_id:
        service = s.get(ServiceInstance, job.service_instance_id)
        if not service:
            raise HTTPException(410, 'recovery service is no longer accepted')
        batch_id = service.orchestration_batch_id or batch_id
    if not batch_id:
        return False
    batch = s.get(EvaluationBatch, batch_id)
    if not batch:
        raise HTTPException(410, 'recovery batch is no longer accepted')
    return bool(batch.deleted_at)


def receive(s, *, worker_id, execution_id, generation, kind, sequence, body, allow_deleted=False):
    if kind not in ('control', 'episode', 'task_result') or type(sequence) is not int or not 0 <= sequence < 2**63:
        raise HTTPException(422, 'invalid recovery record key')
    if not isinstance(body, dict):
        raise HTTPException(422, 'recovery record must be an object')
    try:
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, 'invalid recovery record encoding') from None
    if len(encoded) > MAX_RECORD_BYTES:
        raise HTTPException(413, 'recovery record is too large')
    # All receipts for one execution serialize on its own ledger, not on Run
    # or the global scheduler. Replay and capacity checks share this transaction.
    record = s.scalar(select(ExecutionRecovery).where(
        ExecutionRecovery.execution_id == execution_id).with_for_update())
    if not record or record.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if record.generation != generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    run = s.get(Run, record.run_id)
    if kind != 'control':
        if record.revoked or not run:
            raise HTTPException(410, 'recovery business data is no longer accepted')
        if run.execution_id != execution_id or run.slot_id != record.slot_id:
            raise HTTPException(409, 'recovery execution ownership changed')
        slot = s.get(Slot, record.slot_id)
        if not slot or slot.worker_id != worker_id:
            raise HTTPException(409, 'recovery slot ownership changed')
        job = s.get(Job, run.job_id)
        if not job:
            raise HTTPException(410, 'recovery business data is no longer accepted')
        if batch_deleted(s, job) and not allow_deleted:
            raise HTTPException(410, 'recovery batch is no longer accepted')
    digest = hashlib.sha256(encoded).hexdigest()
    previous = s.get(ExecutionRecoveryReceipt, (execution_id, kind, sequence))
    if previous:
        if previous.digest != digest:
            raise HTTPException(409, 'conflicting recovery record')
        return {'digest': digest, 'duplicate': True, 'reconciled': False}
    max_bytes, max_records = MAX_BYTES, MAX_RECORDS
    if kind != 'control':
        max_bytes -= CONTROL_BYTES
        max_records -= CONTROL_RECORDS
    if record.received_bytes + len(encoded) > max_bytes or record.received_records + 1 > max_records:
        raise HTTPException(413, 'recovery receipt capacity reached')
    s.add(ExecutionRecoveryReceipt(execution_id=execution_id, kind=kind,
        sequence=sequence, digest=digest, payload=json.loads(encoded)))
    record.received_bytes += len(encoded)
    record.received_records += 1
    s.flush()
    return {'digest': digest, 'duplicate': False, 'reconciled': False}


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/recovery/control')
    def control(execution_id: str, body: ControlRecord, worker=Depends(worker_auth), s=Depends(db)):
        result = receive(s, worker_id=worker.id, execution_id=execution_id,
            generation=body.generation, kind='control', sequence=body.sequence, body=body.body)
        s.commit()
        return result

    @app.post('/api/v1/worker/executions/{execution_id}/recovery/results')
    def results(execution_id: str, body: ResultRecord, worker=Depends(worker_auth), s=Depends(db)):
        handler = receive
        if body.apply is True:
            from .recovery_results import apply
            handler = apply
        result = handler(s, worker_id=worker.id, execution_id=execution_id,
            generation=body.generation, kind=body.kind, sequence=body.sequence, body=body.body)
        s.commit()
        return result
