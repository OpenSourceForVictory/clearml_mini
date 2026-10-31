"""Opt-in execution data retention. Only the authenticated Worker performs I/O.

All decisions serialize on the worker row, never the global scheduler lock.
A preview freezes identities; beginning a deletion is a separate authorization.
"""
from datetime import timedelta
from typing import Literal
from uuid import UUID
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func
from .core import Worker, Slot, Run, Job, ExecutionData, DataCleanup, DataCleanupItem, now
from .scheduling import online, utc, TERMINAL


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Identity(Strict):
    root_dev: int = Field(ge=0)
    root_ino: int = Field(ge=1)
    dev: int = Field(ge=0)
    ino: int = Field(ge=1)
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    location: Literal['runs', 'staging'] = 'runs'


class Entry(Strict):
    root_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    root_path: str = Field(min_length=2, max_length=4096)
    entry_name: str = Field(min_length=1, max_length=255)
    generation: str = Field(min_length=36, max_length=36)
    scan_status: Literal['complete', 'partial', 'blocked', 'missing']
    reason: str | None = Field(default=None, max_length=1000)
    identity: Identity | None = None
    logical_bytes: int | None = Field(default=None, ge=0, le=2**63-1)
    allocated_bytes: int | None = Field(default=None, ge=0, le=2**63-1)

    @field_validator('entry_name')
    @classmethod
    def name(cls, value):
        if value in ('.', '..') or '/' in value or '\x00' in value:
            raise ValueError('expected a directory name')
        return value


class Inventory(Strict):
    entries: list[Entry] = Field(max_length=100)


class Selected(Strict):
    id: str
    generation: str


class Selection(Strict):
    entries: list[Selected] = Field(min_length=1, max_length=100)


class Policy(Strict):
    expected_revision: int = Field(ge=0)
    enabled: bool
    days: int = Field(ge=1, le=3650)
    preview_id: str | None = None


class Receipt(Strict):
    status: Literal['succeeded', 'skipped', 'failed']
    reason: str | None = Field(default=None, max_length=1000)
    free_before: int | None = Field(default=None, ge=0)
    free_after: int | None = Field(default=None, ge=0)
    deleted_entries: int = Field(default=0, ge=0)


def machine(s, worker_id):
    w = s.scalar(select(Worker).where(Worker.id == worker_id).with_for_update().execution_options(populate_existing=True))
    if not w:
        raise HTTPException(404, 'node not found')
    return w


def owned_slot(s, worker, slot_id):
    slot = s.get(Slot, slot_id)
    if not slot or slot.worker_id != worker.id:
        raise HTTPException(404, 'slot not found')
    if slot.protocol_version < 6:
        raise HTTPException(409, 'upgrade Worker to protocol v6')
    return slot


def eligibility(s, w, data, generation=None):
    slot = s.get(Slot, data.slot_id)
    if not w.enabled or not slot or not online(slot):
        return 'Worker offline or disabled'
    if slot.protocol_version < 6:
        return 'upgrade Worker to protocol v6'
    if slot.storage_applied_revision != w.storage_revision:
        return 'storage configuration pending'
    if data.scan_status != 'complete' or not data.identity:
        return data.reason or 'complete scan required'
    if not data.last_complete_at or now() - utc(data.last_complete_at) > timedelta(hours=24):
        return 'scan older than 24 hours'
    if generation and data.generation != generation:
        return 'inventory changed; scan and preview again'
    run = s.get(Run, data.run_id) if data.run_id else None
    if not run or run.slot_id != data.slot_id or run.execution_id != data.execution_id:
        return 'directory has no matching execution on this slot'
    if run.status not in TERMINAL or not run.cleanup_confirmed or not run.finished_at:
        return 'execution or container cleanup not finished'
    if data.last_cleanup_status == 'succeeded':
        return 'local data already cleaned'
    return None


def data_view(s, w, d):
    run = s.get(Run, d.run_id) if d.run_id else None
    job = s.get(Job, run.job_id) if run else None
    return {key: getattr(d, key) for key in ('id', 'slot_id', 'execution_id', 'root_path', 'entry_name',
        'generation', 'scan_status', 'reason', 'logical_bytes', 'allocated_bytes', 'scanned_at', 'last_complete_at',
        'last_cleanup_status')} | {'blocked_reason': eligibility(s, w, d), 'job_id': job.id if job else None,
        'job_title': job.title if job else None, 'batch_id': job.batch_id if job else None, 'attempt': run.attempt if run else None}


def operation_view(s, op):
    items = list(s.scalars(select(DataCleanupItem).where(DataCleanupItem.operation_id == op.id).order_by(DataCleanupItem.id)))
    states = {i.status for i in items}
    status = ('preview' if not op.confirmed else 'running' if 'running' in states else
              'queued' if 'queued' in states else 'succeeded' if states == {'succeeded'} else 'completed_with_exceptions')
    return {'id': op.id, 'worker_id': op.worker_id, 'source': op.source, 'policy': op.policy,
        'policy_revision': op.policy_revision, 'storage_revision': op.storage_revision, 'status': status,
        'created_at': op.created_at, 'expires_at': op.expires_at, 'confirmed_at': op.confirmed_at,
        'items': [{'id': i.id, 'data_id': i.data_id, 'slot_id': i.slot_id, 'snapshot': i.snapshot,
                   'status': i.status, 'reason': i.reason, 'receipt': i.receipt} for i in items]}


def preview(s, w, ids, actor=None, source='manual', policy=None):
    op = DataCleanup(worker_id=w.id, actor_id=actor, source=source, policy=policy,
        policy_revision=w.retention_revision if policy else None, storage_revision=w.storage_revision,
        expires_at=now()+timedelta(minutes=10))
    s.add(op)
    s.flush()
    for data_id in dict.fromkeys(ids):
        d = s.get(ExecutionData, data_id)
        if not d or d.worker_id != w.id:
            raise HTTPException(404, 'execution data not found')
        reason = eligibility(s, w, d)
        snapshot = {'root_id': d.root_id, 'root_path': d.root_path, 'entry_name': d.entry_name,
                    'execution_id': d.execution_id, 'identity': d.identity, 'generation': d.generation,
                    'logical_bytes': d.logical_bytes, 'allocated_bytes': d.allocated_bytes}
        s.add(DataCleanupItem(operation_id=op.id, data_id=d.id, slot_id=d.slot_id,
            snapshot=snapshot, status='skipped' if reason else 'preview', reason=reason))
    s.flush()
    return op


def confirm(s, w, op):
    if op.confirmed:
        return
    if utc(op.expires_at) <= now():
        raise HTTPException(409, 'preview expired; preview again')
    if op.storage_revision != w.storage_revision:
        raise HTTPException(409, 'storage configuration changed; preview again')
    if not s.scalar(select(DataCleanupItem.id).where(DataCleanupItem.operation_id == op.id, DataCleanupItem.status == 'preview').limit(1)):
        raise HTTPException(409, 'no eligible entries in this preview')
    op.confirmed = True
    op.confirmed_at = now()
    for item in s.scalars(select(DataCleanupItem).where(DataCleanupItem.operation_id == op.id)):
        if item.status != 'preview':
            continue
        d = s.get(ExecutionData, item.data_id)
        reason = eligibility(s, w, d, item.snapshot['generation'])
        existing = s.scalar(select(DataCleanupItem.id).where(DataCleanupItem.data_id == d.id,
            DataCleanupItem.id != item.id, DataCleanupItem.status.in_(('queued', 'running'))).limit(1))
        item.reason = reason or ('cleanup already pending' if existing else None)
        item.status = 'skipped' if item.reason else 'queued'


def candidates(s, w, days, automatic=False):
    query = select(ExecutionData).join(Run, Run.id == ExecutionData.run_id).where(
        ExecutionData.worker_id == w.id, Run.finished_at <= now()-timedelta(days=days),
        ExecutionData.scan_status == 'complete',
        (ExecutionData.last_cleanup_status.is_(None) | (ExecutionData.last_cleanup_status != 'succeeded'))
    )
    if automatic:
        attempted = select(DataCleanupItem.id).join(DataCleanup).where(DataCleanupItem.data_id == ExecutionData.id,
            DataCleanup.source == 'automatic', DataCleanup.policy_revision == w.retention_revision).exists()
        query = query.join(Slot, Slot.id == ExecutionData.slot_id).where(
            ~attempted, Run.status.in_(TERMINAL), Run.cleanup_confirmed.is_(True),
            ExecutionData.last_complete_at >= now()-timedelta(hours=24),
            Slot.protocol_version >= 6, Slot.last_seen_at >= now()-timedelta(seconds=15),
            Slot.storage_applied_revision == w.storage_revision)
    return list(s.scalars(query.order_by(Run.finished_at, ExecutionData.id).limit(100)))


def sweep(s, w):
    if not w.retention_enabled or (w.retention_checked_at and now()-utc(w.retention_checked_at) < timedelta(hours=1)):
        return
    if s.scalar(select(DataCleanupItem.id).join(DataCleanup).where(DataCleanup.worker_id == w.id,
        DataCleanup.source == 'automatic', DataCleanupItem.status.in_(('queued', 'running'))).limit(1)):
        return
    w.retention_checked_at = now()
    ids = []
    for d in candidates(s, w, w.retention_days, automatic=True):
        if eligibility(s, w, d):
            continue
        # Permanent failures require a fresh administrator preview; never loop on them.
        if s.scalar(select(DataCleanupItem.id).join(DataCleanup).where(DataCleanupItem.data_id == d.id,
            DataCleanup.source == 'automatic', DataCleanup.policy_revision == w.retention_revision).limit(1)):
            continue
        ids.append(d.id)
    if ids:
        op = preview(s, w, ids, source='automatic', policy={'enabled': True, 'days': w.retention_days})
        confirm(s, w, op)


def install(app, db, admin, current, worker_auth):
    @app.get('/api/v1/admin/nodes/{worker_id}/execution-data')
    def inventory(worker_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        rows = s.scalars(select(ExecutionData).where(ExecutionData.worker_id == w.id).order_by(ExecutionData.id).offset(offset).limit(limit))
        return {'items': [data_view(s, w, d) for d in rows], 'total': s.scalar(select(func.count()).select_from(ExecutionData).where(ExecutionData.worker_id == w.id)),
                'scan_revision': w.data_scan_revision, 'policy': {'revision': w.retention_revision, 'enabled': w.retention_enabled, 'days': w.retention_days},
                'slots': [{'id': x.id, 'online': online(x), 'supported': x.protocol_version >= 6} for x in s.scalars(select(Slot).where(Slot.worker_id == w.id))]}

    @app.post('/api/v1/admin/nodes/{worker_id}/execution-data/scan', status_code=202)
    def scan(worker_id: str, user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        w.data_scan_revision += 1
        s.commit()
        return {'scan_revision': w.data_scan_revision, 'status': 'requested'}

    @app.post('/api/v1/admin/nodes/{worker_id}/execution-data/preview')
    def make_preview(worker_id: str, body: Selection, user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        for entry in body.entries:
            d = s.get(ExecutionData, entry.id)
            if not d or d.worker_id != w.id:
                raise HTTPException(404, 'execution data not found')
            if d.generation != entry.generation:
                raise HTTPException(409, 'inventory changed; reload before preview')
        op = preview(s, w, [entry.id for entry in body.entries], user.id)
        s.commit()
        return operation_view(s, op)

    def get_op(s, worker_id, op_id):
        op = s.get(DataCleanup, op_id)
        if not op or op.worker_id != worker_id:
            raise HTTPException(404, 'cleanup operation not found')
        return op

    @app.post('/api/v1/admin/nodes/{worker_id}/execution-data/operations/{op_id}/confirm', status_code=202)
    def submit(worker_id: str, op_id: str, user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        op = get_op(s, w.id, op_id)
        if op.source != 'manual':
            raise HTTPException(409, 'policy previews cannot be manually submitted')
        confirm(s, w, op)
        s.commit()
        return operation_view(s, op)

    @app.get('/api/v1/admin/nodes/{worker_id}/execution-data/operations')
    def operations(worker_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50), user=Depends(admin), s=Depends(db)):
        machine(s, worker_id)
        return [operation_view(s, op) for op in s.scalars(select(DataCleanup).where(DataCleanup.worker_id == worker_id).order_by(DataCleanup.created_at.desc()).offset(offset).limit(limit))]

    @app.post('/api/v1/admin/nodes/{worker_id}/retention/preview')
    def policy_preview(worker_id: str, body: Policy, user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        if body.expected_revision != w.retention_revision:
            raise HTTPException(409, 'policy changed; reload and preview again')
        op = preview(s, w, [d.id for d in candidates(s, w, body.days)], user.id, 'policy_preview', {'enabled': body.enabled, 'days': body.days})
        s.commit()
        return operation_view(s, op)

    @app.put('/api/v1/admin/nodes/{worker_id}/retention')
    def set_policy(worker_id: str, body: Policy, user=Depends(admin), s=Depends(db)):
        w = machine(s, worker_id)
        if body.expected_revision != w.retention_revision:
            raise HTTPException(409, 'policy changed; reload and preview again')
        if body.enabled:
            op = get_op(s, w.id, body.preview_id)
            if (op.source != 'policy_preview' or op.policy_revision != w.retention_revision or
                op.policy != {'enabled': body.enabled, 'days': body.days} or utc(op.expires_at) <= now() or op.storage_revision != w.storage_revision):
                raise HTTPException(409, 'policy preview is stale or differs from draft')
        w.retention_revision += 1
        w.retention_enabled, w.retention_days = body.enabled, body.days
        w.retention_checked_at = None
        # Running operations have already been authorized and may finish recovery.
        for item in s.scalars(select(DataCleanupItem).join(DataCleanup).where(DataCleanup.worker_id == w.id,
            DataCleanup.source == 'automatic', DataCleanupItem.status == 'queued')):
            item.status, item.reason, item.finished_at = 'skipped', 'retention policy changed', now()
        s.commit()
        return {'revision': w.retention_revision, 'enabled': w.retention_enabled, 'days': w.retention_days}

    @app.post('/api/v1/worker/slots/{slot_id}/execution-data/inventory')
    def report(slot_id: str, body: Inventory, worker=Depends(worker_auth), s=Depends(db)):
        w = machine(s, worker.id)
        owned_slot(s, w, slot_id)
        for entry in body.entries:
            d = s.scalar(select(ExecutionData).where(ExecutionData.slot_id == slot_id, ExecutionData.root_id == entry.root_id, ExecutionData.entry_name == entry.entry_name))
            if not d:
                d = ExecutionData(worker_id=w.id, slot_id=slot_id, root_id=entry.root_id, entry_name=entry.entry_name,
                    root_path=entry.root_path, generation=entry.generation, scan_status=entry.scan_status)
                s.add(d)
            try:
                execution = str(UUID(entry.entry_name))
                if execution != entry.entry_name:
                    execution = None
            except ValueError:
                execution = None
            run = s.scalar(select(Run).where(Run.execution_id == execution)) if execution else None
            if run and run.slot_id != slot_id:
                raise HTTPException(409, 'execution belongs to a different slot')
            d.run_id, d.execution_id = (run.id if run else None), execution
            d.root_path, d.scan_status, d.reason, d.scanned_at = entry.root_path, entry.scan_status, entry.reason, now()
            # Partial reports explicitly invalidate eligibility without erasing the last estimate.
            if entry.scan_status == 'complete':
                if not entry.identity or entry.logical_bytes is None or entry.allocated_bytes is None:
                    raise HTTPException(422, 'complete inventory needs identity and estimates')
                d.generation, d.identity = entry.generation, entry.identity.model_dump()
                d.logical_bytes, d.allocated_bytes, d.last_complete_at = entry.logical_bytes, entry.allocated_bytes, now()
            elif not d.generation:
                d.generation, d.identity = entry.generation, {}
        s.commit()
        return {'accepted': len(body.entries)}

    @app.post('/api/v1/worker/slots/{slot_id}/execution-data/control')
    def control(slot_id: str, worker=Depends(worker_auth), s=Depends(db)):
        w = machine(s, worker.id)
        owned_slot(s, w, slot_id)
        sweep(s, w)
        items = list(s.scalars(select(DataCleanupItem).join(DataCleanup).where(DataCleanup.worker_id == w.id,
            DataCleanupItem.slot_id == slot_id, DataCleanupItem.status.in_(('queued', 'running'))).order_by(DataCleanup.created_at).limit(10)))
        result = {'scan_revision': w.data_scan_revision, 'items': [{'id': i.id, 'status': i.status, 'snapshot': i.snapshot} for i in items]}
        s.commit()
        return result

    def item_for(s, worker, slot_id, item_id):
        w = machine(s, worker.id)
        owned_slot(s, w, slot_id)
        item = s.get(DataCleanupItem, item_id)
        if not item or item.slot_id != slot_id:
            raise HTTPException(404, 'cleanup item not found')
        return w, item, s.get(DataCleanup, item.operation_id)

    @app.post('/api/v1/worker/slots/{slot_id}/execution-data/{item_id}/begin')
    def begin(slot_id: str, item_id: str, worker=Depends(worker_auth), s=Depends(db)):
        w, item, op = item_for(s, worker, slot_id, item_id)
        if item.status not in ('queued', 'running'):
            raise HTTPException(409, 'cleanup item is not pending')
        reason = eligibility(s, w, s.get(ExecutionData, item.data_id), item.snapshot['generation'])
        if op.storage_revision != w.storage_revision:
            reason = 'storage configuration changed'
        if op.source == 'automatic' and (not w.retention_enabled or op.policy_revision != w.retention_revision):
            reason = 'retention policy changed'
        if reason:
            if item.status == 'queued':
                item.status, item.reason, item.finished_at = 'skipped', reason, now()
                s.commit()
            raise HTTPException(409, reason)
        item.status = 'running'
        item.started_at = item.started_at or now()
        item.authorized_until = now()+timedelta(seconds=30)
        s.commit()
        return {'id': item.id, 'authorized_until': item.authorized_until, 'snapshot': item.snapshot}

    @app.post('/api/v1/worker/slots/{slot_id}/execution-data/{item_id}/receipt')
    def receipt(slot_id: str, item_id: str, body: Receipt, worker=Depends(worker_auth), s=Depends(db)):
        w, item, op = item_for(s, worker, slot_id, item_id)
        value = body.model_dump()
        if item.receipt:
            if item.receipt != value:
                raise HTTPException(409, 'receipt already recorded with different contents')
            return {'accepted': True}
        if item.status not in ('queued', 'running') and not (item.status == 'skipped' and body.status == 'skipped'):
            raise HTTPException(409, 'cleanup item is no longer pending')
        if item.status != 'running' and body.status == 'succeeded':
            raise HTTPException(409, 'cleanup was not authorized')
        item.status, item.reason, item.receipt, item.finished_at = body.status, body.reason, value, now()
        d = s.get(ExecutionData, item.data_id)
        d.last_cleanup_status = body.status
        if body.status == 'succeeded':
            d.scan_status, d.reason = 'missing', 'local execution data cleaned'
        s.commit()
        return {'accepted': True}

    @app.get('/api/v1/jobs/{job_id}/execution-data')
    def job_data(job_id: str, user=Depends(current), s=Depends(db)):
        job = s.get(Job, job_id)
        if not job or (user.role != 'admin' and job.owner_id != user.id):
            raise HTTPException(404, 'job not found')
        return [{'execution_id': d.execution_id, 'status': d.last_cleanup_status} for d in s.scalars(
            select(ExecutionData).join(Run).where(Run.job_id == job_id, ExecutionData.last_cleanup_status.is_not(None)))]
