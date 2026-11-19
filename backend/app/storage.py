"""Administrator intent and slot acknowledgement, serialized by scheduler lock."""
from pathlib import PurePosixPath
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, or_
from .core import Worker, Slot, Run, now
from .scheduling import lock, online, transition
from .freshness import ENVIRONMENT_REPORT_SECONDS, recent


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    workspace_root: str = Field(min_length=2, max_length=4096)
    assets_root: str = Field(min_length=2, max_length=4096)
    cache_root: str = Field(min_length=2, max_length=4096)
    artifact_root: str | None = Field(default=None, min_length=2, max_length=4096)
    min_free_disk_gb: float = Field(default=2, ge=0, le=2097151)

    @field_validator('workspace_root', 'assets_root', 'cache_root', 'artifact_root')
    @classmethod
    def path(cls, value):
        if value is None:return None
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


OVERRIDES = 'slot_storage_overrides'


def base_config(worker):
    return worker.storage_config


def slot_config(worker, slot):
    return (worker.connection or {}).get(OVERRIDES, {}).get(slot.id)


def desired(worker, slot=None):
    config = slot_config(worker, slot) if slot else None
    base = base_config(worker)
    if config is not None and not config.get('artifact_root') and (base or {}).get('artifact_root'):
        config = {**config, 'artifact_root': base['artifact_root']}
    return {'revision': worker.storage_revision,
            'config': config if config is not None else base}


def held(s, slot):
    return bool(s.scalar(select(Run.id).where(Run.slot_id == slot.id,
        or_(Run.status.in_(('STARTING', 'RUNNING', 'CANCEL_REQUESTED')), Run.cleanup_confirmed.is_(False))).limit(1)))


def ready(worker, slot, at):
    if not worker.storage_revision and slot.protocol_version < 4:
        return True
    config = desired(worker, slot)['config'] or (slot.storage_report or {}).get('effective') or {}
    if config.get('artifact_root') and 'artifact_roots_v1' not in (slot.software or {}).get('capabilities', []):
        return False
    report = slot.storage_report or {}
    if config.get('artifact_root') != (report.get('effective') or {}).get('artifact_root'):
        return False
    return (slot.protocol_version >= 4 and slot.storage_applied_revision == worker.storage_revision
            and report.get('revision') == worker.storage_revision and report.get('status') == 'applied'
            and recent(slot.storage_reported_at, at, ENVIRONMENT_REPORT_SECONDS))


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
        config = desired(worker, slot)['config']
        if config:
            expected = dict(config)
            expected['workspace_root'] += '/slots/' + slot.id
            if report.effective.model_dump(exclude_none=True) != {k:v for k,v in expected.items() if v is not None}:
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
        elif (desired(worker,slot)['config'] or {}).get('artifact_root') and 'artifact_roots_v1' not in (slot.software or {}).get('capabilities',[]):
            status = 'upgrade_required'
        elif slot.protocol_version < 4:
            status = 'upgrade_required' if worker.storage_revision else 'local'
        elif not recent(slot.storage_reported_at, now(), ENVIRONMENT_REPORT_SECONDS):
            status = 'offline'
        elif report.get('revision') != worker.storage_revision:
            status = 'pending'
        elif (desired(worker,slot)['config'] is not None and report.get('status') == 'applied'
              and desired(worker,slot)['config'].get('artifact_root') != (report.get('effective') or {}).get('artifact_root')):
            status = 'pending'
        else:
            status = report.get('status', 'pending')
        slots.append({'slot_id': slot.id, 'gpu_index': slot.gpu_index, 'accelerator_type': slot.accelerator_type,
            'config': slot_config(worker, slot), 'status': status,
            'applied_revision': slot.storage_applied_revision, 'report': report,
            'reported_at': slot.storage_reported_at})
    return {'worker_id': worker.id, **desired(worker), 'slots': slots,
            'all_applied': bool(slots) and all(x['status'] == 'applied' for x in slots)}


def install(app, db, admin):
    def machine(s, worker_id):
        w = s.scalar(select(Worker).where(Worker.id == worker_id).with_for_update().execution_options(populate_existing=True))
        if not w:
            raise HTTPException(404, 'node not found')
        return w

    def release_reserved(s, slot_ids):
        for run in s.scalars(select(Run).where(Run.slot_id.in_(slot_ids), Run.status == 'RESERVED')):
            transition(s, run, 'PENDING', 'storage configuration changed before claim')
            run.slot_id = run.execution_id = run.queue_name = run.reservation_expires_at = run.reserved_at = None
            run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = 0
            run.allocation_index = 0

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
        w.storage_config=body.config.model_dump(exclude_none=True) if body.config else None
        ids = select(Slot.id).where(Slot.worker_id == w.id)
        release_reserved(s, ids)
        s.commit()
        return view(s, w)

    @app.put('/api/v1/admin/nodes/{worker_id}/storage/slots/{slot_id}')
    def put_slot_storage(worker_id: str, slot_id: str, body: StorageUpdate, user=Depends(admin), s=Depends(db)):
        lock(s)
        w = machine(s, worker_id)
        s.refresh(w, with_for_update=True)
        slot=s.get(Slot,slot_id)
        if not slot or slot.worker_id!=w.id or getattr(slot, 'deleted_at', None):
            raise HTTPException(404, 'device not found on this machine')
        if body.expected_revision != w.storage_revision:
            raise HTTPException(409, 'storage configuration changed; reload before saving')
        overrides=dict((w.connection or {}).get(OVERRIDES, {}))
        if body.config:
            overrides[slot_id]=body.config.model_dump(exclude_none=True)
        else:
            overrides.pop(slot_id,None)
        connection=dict(w.connection or {})
        if overrides:connection[OVERRIDES]=overrides
        else:connection.pop(OVERRIDES,None)
        w.connection=connection or None
        w.storage_revision+=1
        release_reserved(s,[slot_id])
        s.commit()
        return view(s,w)
