"""Administrator intent and slot acknowledgement, serialized by scheduler lock."""
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, or_
from .core import Worker, Slot, Run, now
from .scheduling import lock, online, transition, utc


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    workspace_root: str = Field(min_length=2, max_length=4096)
    assets_root: str = Field(min_length=2, max_length=4096)
    cache_root: str = Field(min_length=2, max_length=4096)
    min_free_disk_gb: float = Field(default=2, ge=0, le=2097151)

    @field_validator('workspace_root', 'assets_root', 'cache_root')
    @classmethod
    def path(cls, value):
        p = PurePosixPath(value)
        if not p.is_absolute() or str(p) == '/' or '..' in p.parts or ',' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('expected an absolute directory without control characters, commas or ..')
        return str(p)


class StorageUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    config: StorageConfig | None


class StorageReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    applied_revision: int = Field(ge=0)
    status: Literal['applied', 'waiting', 'failed']
    effective: StorageConfig
    capacity: dict[Literal['workspace', 'assets', 'cache'], int] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)

    @field_validator('capacity')
    @classmethod
    def capacity_range(cls, value):
        if any(not 0 <= v <= 2147483647 for v in value.values()):
            raise ValueError('invalid directory free space')
        return value


def desired(worker):
    return {'revision': worker.storage_revision, 'config': worker.storage_config}


def held(s, slot):
    return bool(s.scalar(select(Run.id).where(Run.slot_id == slot.id,
        or_(Run.status.in_(('STARTING', 'RUNNING', 'CANCEL_REQUESTED')), Run.cleanup_confirmed.is_(False))).limit(1)))


def ready(worker, slot, at):
    if not worker.storage_revision and slot.protocol_version < 4:
        return True
    report = slot.storage_report or {}
    return (slot.protocol_version >= 4 and slot.storage_applied_revision == worker.storage_revision
            and report.get('revision') == worker.storage_revision and report.get('status') == 'applied'
            and slot.storage_reported_at is not None
            and timedelta(0) <= at - utc(slot.storage_reported_at) < timedelta(seconds=15))


def acknowledge(s, worker, slot, report):
    if slot.protocol_version < 4:
        raise HTTPException(409, 'storage reporting requires worker protocol v4')
    # A stale heartbeat never overwrites a newer report or opens admission.
    if report.revision != worker.storage_revision:
        return
    if report.status == 'applied':
        if report.applied_revision != report.revision:
            raise HTTPException(409, 'storage acknowledgement revision mismatch')
        if slot.storage_applied_revision != report.revision and held(s, slot):
            raise HTTPException(409, 'storage switch must wait for execution cleanup')
        if worker.storage_config:
            expected = dict(worker.storage_config)
            expected['workspace_root'] += '/slots/' + slot.id
            if report.effective.model_dump() != expected:
                raise HTTPException(409, 'effective storage differs from desired configuration')
        slot.storage_applied_revision = report.applied_revision
    slot.storage_report = report.model_dump()
    slot.storage_reported_at = now()


def view(s, worker):
    slots = []
    for slot in s.scalars(select(Slot).where(Slot.worker_id == worker.id).order_by(Slot.gpu_index)):
        report = slot.storage_report or {}
        if not online(slot):
            status = 'offline'
        elif slot.protocol_version < 4:
            status = 'upgrade_required' if worker.storage_revision else 'local'
        elif not slot.storage_reported_at or (now()-utc(slot.storage_reported_at)).total_seconds() >= 15:
            status = 'offline'
        elif report.get('revision') != worker.storage_revision:
            status = 'pending'
        else:
            status = report.get('status', 'pending')
        slots.append({'slot_id': slot.id, 'gpu_index': slot.gpu_index, 'status': status,
            'applied_revision': slot.storage_applied_revision, 'report': report,
            'reported_at': slot.storage_reported_at})
    return {'worker_id': worker.id, **desired(worker), 'slots': slots,
            'all_applied': bool(slots) and all(x['status'] == 'applied' for x in slots)}


def install(app, db, admin):
    def machine(s, worker_id):
        w = s.get(Worker, worker_id)
        if not w:
            raise HTTPException(404, 'node not found')
        return w

    @app.get('/api/v1/admin/nodes/{worker_id}/storage')
    def get_storage(worker_id: str, user=Depends(admin), s=Depends(db)):
        return view(s, machine(s, worker_id))

    @app.put('/api/v1/admin/nodes/{worker_id}/storage')
    def put_storage(worker_id: str, body: StorageUpdate, user=Depends(admin), s=Depends(db)):
        lock(s)
        w = machine(s, worker_id)
        if body.expected_revision != w.storage_revision:
            raise HTTPException(409, 'storage configuration changed; reload before saving')
        w.storage_revision += 1
        w.storage_config = body.config.model_dump() if body.config else None
        ids = select(Slot.id).where(Slot.worker_id == w.id)
        for run in s.scalars(select(Run).where(Run.slot_id.in_(ids), Run.status == 'RESERVED')):
            transition(s, run, 'PENDING', 'storage configuration changed before claim')
            run.slot_id = run.execution_id = run.queue_name = run.reservation_expires_at = run.reserved_at = None
            run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = 0
            run.allocation_index = 0
        s.commit()
        return view(s, w)
