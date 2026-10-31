"""Versioned administrator intent and per-resource worker readiness."""
import re
from datetime import timedelta
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from .core import Worker, Slot, Run, Job, now
from .scheduling import lock, transition, utc, online
from .storage import held, StorageConfig
from runner.image_runtime import NAME


class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    images: list[str] = Field(default_factory=list, max_length=100)
    assets: dict[str, str] = Field(default_factory=dict, max_length=16)

    @field_validator('images')
    @classmethod
    def images_valid(cls, value):
        if any(len(x) > 500 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]*', x) for x in value):
            raise ValueError('invalid image reference')
        return list(dict.fromkeys(value))

    @field_validator('assets')
    @classmethod
    def assets_valid(cls, value):
        if any(not NAME.fullmatch(k) or len(v) > 4096 for k, v in value.items()):
            raise ValueError('invalid logical asset name')
        return {k: StorageConfig.path(v) for k, v in value.items()}


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    config: Config | None


class Resource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ready: bool
    error: str | None = Field(default=None, max_length=1000)


class Report(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0)
    applied_revision: int = Field(ge=0)
    effective: Config
    images: dict[str, Resource] = Field(default_factory=dict, max_length=100)
    assets: dict[str, Resource] = Field(default_factory=dict, max_length=16)


def desired(worker):
    return {'revision': worker.runtime_revision, 'config': worker.runtime_config}


def ready(worker, slot, at):
    if slot.protocol_version < 5:
        return not worker.runtime_revision
    r = slot.runtime_report or {}
    return (r.get('revision') == worker.runtime_revision == r.get('applied_revision')
            and slot.runtime_reported_at is not None
            and timedelta(0) <= at-utc(slot.runtime_reported_at) < timedelta(seconds=15))


def blockers(worker, slot, spec, at):
    if not ready(worker, slot, at):
        return ['节点运行配置尚未生效或报告已过期']
    runtime = (spec or {}).get('template', {}).get('runtime')
    if runtime is None:
        if spec and (slot.protocol_version < 3 or spec['template']['image'] not in (slot.client_images or [])):
            return ['所需镜像未批准或未就绪']
        return []
    if slot.protocol_version < 5:
        return ['需要 Worker v5']
    report = slot.runtime_report or {}
    result = []
    if not report.get('images', {}).get(spec['template']['image'], {}).get('ready'):
        result.append('所需镜像未批准或未就绪')
    for name in runtime['assets']:
        if not report.get('assets', {}).get(name, {}).get('ready'):
            result.append('逻辑资产未就绪: '+name)
    return result


def acknowledge(s, worker, slot, report):
    if slot.protocol_version < 5:
        raise HTTPException(409, 'runtime reporting requires worker v5')
    if report.revision != worker.runtime_revision:
        return
    if report.applied_revision > report.revision:
        raise HTTPException(409, 'invalid applied runtime revision')
    old = (slot.runtime_report or {}).get('applied_revision', 0)
    if old != report.applied_revision and held(s, slot):
        raise HTTPException(409, 'runtime switch must wait for cleanup')
    if report.applied_revision == report.revision and worker.runtime_config is not None and report.effective.model_dump() != worker.runtime_config:
        raise HTTPException(409, 'effective runtime differs from desired configuration')
    if set(report.images) - set(report.effective.images) or set(report.assets) - set(report.effective.assets):
        raise HTTPException(409, 'reported resources not in effective configuration')
    slot.runtime_report = report.model_dump()
    slot.runtime_reported_at = now()
    # Legacy templates also benefit from the effective image approval list.
    slot.client_images = [k for k, v in report.images.items() if v.ready]


def view(s, worker):
    return {'worker_id': worker.id, **desired(worker), 'slots': [
        {'slot_id': slot.id, 'gpu_index': slot.gpu_index, 'ready': online(slot) and ready(worker, slot, now()),
         'report': slot.runtime_report, 'reported_at': slot.runtime_reported_at}
        for slot in s.scalars(select(Slot).where(Slot.worker_id == worker.id).order_by(Slot.gpu_index))]}


def install(app, db, admin, current):
    def machine(s, identity):
        worker = s.get(Worker, identity)
        if not worker:
            raise HTTPException(404, 'node not found')
        return worker

    @app.get('/api/v1/admin/nodes/{worker_id}/runtime')
    def get(worker_id: str, user=Depends(admin), s=Depends(db)):
        return view(s, machine(s, worker_id))

    @app.put('/api/v1/admin/nodes/{worker_id}/runtime')
    def put(worker_id: str, body: Update, user=Depends(admin), s=Depends(db)):
        lock(s)
        worker = machine(s, worker_id)
        if body.expected_revision != worker.runtime_revision:
            raise HTTPException(409, 'runtime configuration changed; reload before saving')
        worker.runtime_revision += 1
        worker.runtime_config = body.config.model_dump() if body.config is not None else None
        ids = select(Slot.id).where(Slot.worker_id == worker.id)
        for run in s.scalars(select(Run).where(Run.slot_id.in_(ids), Run.status == 'RESERVED')):
            transition(s, run, 'PENDING', 'node runtime configuration changed before claim')
            run.slot_id = run.execution_id = run.queue_name = run.reservation_expires_at = run.reserved_at = None
            run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = run.allocation_index = 0
        s.commit()
        return view(s, worker)

    @app.get('/api/v1/jobs/{job_id}/runtime-readiness')
    def readiness(job_id: str, user=Depends(current), s=Depends(db)):
        job = s.get(Job, job_id)
        if not job or (job.owner_id != user.id and user.role != 'admin'):
            raise HTTPException(404, 'job not found')
        result = []
        for slot in s.scalars(select(Slot).where(Slot.pool_id == job.pool_id)):
            worker = s.get(Worker, slot.worker_id)
            reasons = blockers(worker, slot, job.client_spec, now())
            if not online(slot) or not worker.enabled:
                reasons.insert(0, '节点离线或已停用')
            result.append({'slot_id': slot.id, 'node_name': worker.node_name, 'gpu_index': slot.gpu_index, 'runtime_ready': not reasons, 'reasons': reasons})
        return result
