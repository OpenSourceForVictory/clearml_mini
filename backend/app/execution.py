"""Authenticated worker protocol and administrator provisioning."""
import hashlib
import base64
import binascii
import os
import secrets
from datetime import timedelta
from typing import Literal, Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict
from argon2 import PasswordHasher
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from . import storage as storage_protocol
from .core import Job, Pool, Run, RunArtifact, RunLog, Slot, User, Worker, now
from .scheduling import ACTIVE, TERMINAL, lock, online, seconds, transition, utc, execution_timeout
from runner.cs_contract import MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL, MAX_ARTIFACT_COUNT, artifact_name

MAX_LOG_BYTES = 262144


class Register(BaseModel):
    pool_id: str
    gpu_index: int = Field(ge=0, le=1023)
    gpu_uuid: str = Field(min_length=1, max_length=100)
    gpu_model: str = Field(min_length=1, max_length=100)
    vram_gb: int = Field(ge=0, le=10000)
    vram_mb: Optional[int] = Field(default=None, ge=0, le=10240000)
    protocol_version: Literal[1, 2, 3, 4] = 1
    client_images: list[str] = Field(default_factory=list, max_length=100)


class Telemetry(BaseModel):
    free_vram_mb: int = Field(ge=0, le=10240000)
    gpu_utilization: int = Field(ge=0, le=100)


class DiskTelemetry(BaseModel):
    free_mb: int = Field(ge=0, le=2147483647)
    min_free_mb: int = Field(ge=0, le=2147483647)


class Claim(BaseModel):
    known_execution_ids: list[str] = Field(default_factory=list, max_length=1024)


class Heartbeat(BaseModel):
    slot_id: str
    execution_id: Optional[str] = None
    started: bool = True
    telemetry: Optional[Telemetry] = None
    telemetry_failed: bool = False
    disk: Optional[DiskTelemetry] = None
    storage: Optional[storage_protocol.StorageReport] = None


class Metrics(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    success_rate: float = Field(ge=0, le=1)
    episode_count: int = Field(ge=1, le=10000)
    runtime: float = Field(ge=0)


class Finish(BaseModel):
    status: Literal['SUCCEEDED', 'FAILED', 'CANCELLED']
    metrics: Optional[Metrics] = None
    error: Optional[str] = Field(default=None, max_length=1000)


class LogBody(BaseModel):
    sequence: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=8192)


class ArtifactUpload(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(max_length=4*((MAX_ARTIFACT_BYTES+2)//3))


class WorkerCreate(BaseModel):
    node_name: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    max_concurrent: int = Field(default=1, ge=1, le=1024)
    cpu_capacity: int = Field(default=4, ge=1, le=10000)
    ram_gb_capacity: int = Field(default=8, ge=1, le=100000)


class PoolPolicy(BaseModel):
    allow_shared: bool = False
    max_tasks_per_gpu: int = Field(default=1, ge=1, le=32)
    vram_reserve_gb: int = Field(default=2, ge=0, le=10000)
    max_gpu_utilization: int = Field(default=90, ge=1, le=100)


class PoolCreate(PoolPolicy):
    name: str = Field(min_length=1, max_length=100)
    gpu_model: str = Field(default='*', min_length=1, max_length=100)
    min_vram_gb: int = Field(default=0, ge=0, le=10000)
    cpu_per_job: int = Field(default=1, ge=1, le=10000)
    ram_gb_per_job: int = Field(default=1, ge=1, le=100000)


class Enabled(BaseModel):
    enabled: Optional[bool] = None
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=1024)
    cpu_capacity: Optional[int] = Field(default=None, ge=1, le=10000)
    ram_gb_capacity: Optional[int] = Field(default=None, ge=1, le=100000)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    role: Literal['user', 'admin'] = 'user'


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def install(app, db, current):
    bearer = HTTPBearer(auto_error=False)

    def worker_auth(token: Optional[HTTPAuthorizationCredentials] = Depends(bearer), s: Session = Depends(db)):
        if not token or not token.credentials.startswith('wk_'):
            raise HTTPException(401, 'worker credential required')
        w = s.scalar(select(Worker).where(Worker.token_hash == token_hash(token.credentials), Worker.enabled))
        if not w:
            raise HTTPException(401, 'invalid worker credential')
        return w

    def admin(user=Depends(current)):
        if user.role != 'admin':
            raise HTTPException(403, 'admin required')
        return user

    storage_protocol.install(app, db, admin)

    def slot_for(s, slot_id, worker):
        slot = s.get(Slot, slot_id)
        if not slot or slot.worker_id != worker.id:
            raise HTTPException(404, 'slot not found')
        return slot

    def run_for(s, execution_id, worker):
        run = s.scalar(select(Run).where(Run.execution_id == execution_id))
        if not run:
            raise HTTPException(404, 'execution not found')
        slot_for(s, run.slot_id, worker)
        return run

    def live(run):
        if run.status not in ('STARTING', 'RUNNING', 'CANCEL_REQUESTED') or not run.lease_expires_at or utc(run.lease_expires_at) <= now():
            raise HTTPException(409, 'execution lease is no longer valid')

    @app.post('/api/v1/admin/users', status_code=201)
    def create_user(body: UserCreate, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        if s.scalar(select(User).where(User.username == body.username)):
            raise HTTPException(409, 'username already exists')
        account = User(username=body.username, role=body.role, password_hash=PasswordHasher().hash(body.password))
        s.add(account)
        s.commit()
        return {'id': account.id, 'username': account.username, 'role': account.role}

    @app.post('/api/v1/admin/worker-credentials', status_code=201)
    def provision(body: WorkerCreate, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        if s.scalar(select(Worker).where(Worker.node_name == body.node_name)):
            raise HTTPException(409, 'node already provisioned')
        token = 'wk_' + secrets.token_urlsafe(32)
        worker = Worker(token_hash=token_hash(token), **body.model_dump())
        s.add(worker)
        s.commit()
        return {'id': worker.id, 'token': token, 'node_name': worker.node_name}

    @app.get('/api/v1/admin/nodes')
    def nodes(user=Depends(admin), s: Session = Depends(db)):
        return [{'id': w.id, 'node_name': w.node_name, 'enabled': w.enabled,
                 'max_concurrent': w.max_concurrent, 'cpu_capacity': w.cpu_capacity,
                 'ram_gb_capacity': w.ram_gb_capacity} for w in s.scalars(select(Worker))]

    @app.post('/api/v1/admin/nodes/{worker_id}/rotate-token')
    def rotate(worker_id: str, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        w = s.get(Worker, worker_id)
        if not w:
            raise HTTPException(404, 'node not found')
        token = 'wk_' + secrets.token_urlsafe(32)
        w.token_hash = token_hash(token)
        s.commit()
        return {'id': w.id, 'node_name': w.node_name, 'token': token}

    @app.patch('/api/v1/admin/nodes/{worker_id}')
    def enable_node(worker_id: str, body: Enabled, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        w = s.get(Worker, worker_id)
        if not w:
            raise HTTPException(404, 'node not found')
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(w, key, value)
        s.commit()
        return {'enabled': w.enabled}

    @app.post('/api/v1/admin/pools', status_code=201)
    def create_pool(body: PoolCreate, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        if s.scalar(select(Pool).where(Pool.name == body.name)):
            raise HTTPException(409, 'pool name already exists')
        values = body.model_dump()
        values['gpu_model'] = 'virtual' if body.gpu_model == 'virtual' else '*'
        p = Pool(**values)
        s.add(p)
        s.commit()
        return {'id': p.id, **values}

    @app.patch('/api/v1/admin/pools/{pool_id}')
    def update_pool(pool_id: str, body: PoolPolicy, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        pool = s.get(Pool, pool_id)
        if not pool:
            raise HTTPException(404, 'pool not found')
        for key, value in body.model_dump().items():
            setattr(pool, key, value)
        s.commit()
        return {'id': pool.id, **body.model_dump()}

    @app.post('/api/v1/worker/register')
    def register(body: Register, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        pool = s.get(Pool, body.pool_id)
        if not pool or not pool.enabled or pool.min_vram_gb > body.vram_gb:
            raise HTTPException(400, 'device does not match pool requirements')
        if (pool.gpu_model == 'virtual') != body.gpu_uuid.startswith('mock:') or (body.gpu_model == 'virtual') != body.gpu_uuid.startswith('mock:'):
            raise HTTPException(400, 'virtual and physical device identities must be distinct')
        existing = s.scalar(select(Slot).where(Slot.gpu_uuid == body.gpu_uuid))
        indexed = s.scalar(select(Slot).where(Slot.node_name == worker.node_name, Slot.gpu_index == body.gpu_index))
        if existing and (existing.worker_id != worker.id or existing.gpu_index != body.gpu_index):
            raise HTTPException(409, 'GPU UUID already registered to another slot')
        if indexed and indexed.gpu_uuid != body.gpu_uuid:
            raise HTTPException(409, 'GPU index already registered to another UUID')
        slot = existing or indexed
        if slot and slot.pool_id != pool.id:
            held = s.scalar(select(Run.id).where(Run.slot_id == slot.id,
                or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False))).limit(1))
            if held:
                raise HTTPException(409, 'cannot move a slot with held executions')
            slot.pool_id = pool.id
        if not slot:
            slot = Slot(node_name=worker.node_name, gpu_index=body.gpu_index, gpu_uuid=body.gpu_uuid,
                        pool_id=pool.id, worker_id=worker.id, queue_name=f'{worker.id}-{body.gpu_index}', agent_id=worker.id)
            s.add(slot)
        slot.gpu_model = body.gpu_model
        slot.vram_mb = body.vram_mb if body.vram_mb is not None else body.vram_gb * 1024
        slot.protocol_version = body.protocol_version
        slot.client_images = body.client_images if body.protocol_version >= 3 else []
        slot.storage_reported_at = None
        slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        slot.last_seen_at = slot.heartbeat_at = now()
        s.commit()
        return {'slot_id': slot.id, 'quarantined': slot.quarantined}

    @app.post('/api/v1/worker/heartbeat')
    def heartbeat(body: Heartbeat, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        slot = slot_for(s, body.slot_id, worker)
        s.refresh(worker)
        cancel = False
        if body.execution_id:
            run = run_for(s, body.execution_id, worker)
            if run.slot_id != slot.id:
                raise HTTPException(409, 'execution belongs to another slot')
            live(run)
            if run.status == 'STARTING' and body.started:
                run.started_at = s.get(Job, run.job_id).started_at = now()
                transition(s, run, 'RUNNING')
            run.lease_expires_at = now() + timedelta(seconds=seconds('LEASE_SECONDS', 30))
            cancel = run.status == 'CANCEL_REQUESTED'
        if not body.execution_id and body.storage is not None:
            storage_protocol.acknowledge(s, worker, slot, body.storage)
        if body.disk is not None and not body.execution_id:
            slot.disk_free_mb = body.disk.free_mb
            slot.disk_min_free_mb = body.disk.min_free_mb
            slot.disk_at = now()
        if body.telemetry_failed:
            slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        elif body.telemetry is not None:
            if body.telemetry.free_vram_mb > slot.vram_mb:
                raise HTTPException(400, 'free VRAM exceeds device capacity')
            slot.free_vram_mb = body.telemetry.free_vram_mb
            slot.gpu_utilization = body.telemetry.gpu_utilization
            slot.telemetry_at = now()
        slot.last_seen_at = slot.heartbeat_at = now()
        s.commit()
        return {'lease_seconds': seconds('LEASE_SECONDS', 30), 'cancel': cancel,
                'storage': storage_protocol.desired(worker), 'storage_busy': storage_protocol.held(s, slot)}

    @app.post('/api/v1/worker/slots/{slot_id}/claim')
    def claim(slot_id: str, body: Optional[Claim] = None, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        slot = slot_for(s, slot_id, worker)
        cleanup = list(s.scalars(select(Run.execution_id).where(Run.slot_id == slot.id, Run.cleanup_confirmed.is_(False), Run.status.in_(TERMINAL))))
        if cleanup:
            return {'task': None, 'cleanup': cleanup}
        known = body.known_execution_ids if body else []
        run = s.scalar(select(Run).where(Run.slot_id == slot.id, Run.status.in_(ACTIVE),
            Run.execution_id.not_in(known)).order_by(Run.reserved_at, Run.id).limit(1))
        if not run or not online(slot) or slot.quarantined:
            return {'task': None, 'cleanup': []}
        if run.status == 'RESERVED':
            s.refresh(worker)
            if not storage_protocol.ready(worker, slot, now()):
                return {'task': None, 'cleanup': []}
            if slot.disk_min_free_mb is not None and (slot.disk_free_mb is None or slot.disk_free_mb < slot.disk_min_free_mb
                    or slot.disk_at is None or (now()-utc(slot.disk_at)).total_seconds() >= 15):
                return {'task': None, 'cleanup': []}
            if utc(run.reservation_expires_at) <= now():
                return {'task': None, 'cleanup': []}
            run.enqueued_at = now()
            run.lease_expires_at = now() + timedelta(seconds=seconds('LEASE_SECONDS', 30))
            run.cleanup_confirmed = False
            transition(s, run, 'STARTING')
        else:
            live(run)
        job, pool = s.get(Job, run.job_id), s.get(Pool, slot.pool_id)
        task = {'storage_revision': slot.storage_applied_revision, 'run_id': run.id, 'execution_id': run.execution_id, 'job_id': job.id,
                'benchmark': job.benchmark, 'model_ref': job.model_ref, 'episodes': job.episodes,
                'status': run.status, 'image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:dev'),
                'gpu_uuid': None if pool.gpu_model == 'virtual' else slot.gpu_uuid,
                'cpus': run.reserved_cpu, 'ram_gb': run.reserved_ram_gb,
                'gpu_mode': 'exclusive' if run.exclusive else 'shared',
                'requested_vram_mb': job.requested_vram_gb * 1024,
                'vram_reserve_mb': pool.vram_reserve_gb * 1024,
                'timeout_seconds': execution_timeout(job),
                'lease_seconds': seconds('LEASE_SECONDS', 30)}
        if job.client_spec:
            task['client_spec'] = job.client_spec
            task['image'] = job.client_spec['template']['image']
        s.commit()
        return {'task': task, 'cleanup': []}

    @app.post('/api/v1/worker/executions/{execution_id}/logs')
    def logs(execution_id: str, body: LogBody, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        run = run_for(s, execution_id, worker)
        live(run)
        previous = s.get(RunLog, (run.id, body.sequence))
        if previous:
            if previous.content != body.content:
                raise HTTPException(409, 'log sequence already used')
            return {'accepted': True}
        expected = s.scalar(select(func.count()).select_from(RunLog).where(RunLog.run_id == run.id))
        if body.sequence != expected:
            raise HTTPException(409, 'log sequence gap')
        size = len(body.content.encode())
        if run.log_bytes + size > MAX_LOG_BYTES:
            raise HTTPException(413, 'run log limit reached')
        s.add(RunLog(run_id=run.id, sequence=body.sequence, content=body.content))
        run.log_bytes += size
        s.commit()
        return {'accepted': True}

    @app.post('/api/v1/worker/executions/{execution_id}/episodes')
    def episode(execution_id: str, body: dict, worker=Depends(worker_auth), s: Session = Depends(db)):
        from .episodes import record
        lock(s)
        run = run_for(s, execution_id, worker)
        live(run)
        record(s, run, body)
        s.commit()
        return {'accepted': True}

    @app.post('/api/v1/worker/executions/{execution_id}/artifacts')
    def upload_artifact(execution_id: str, body: ArtifactUpload, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        run = run_for(s, execution_id, worker)
        live(run)
        if not artifact_name(body.name):
            raise HTTPException(400, 'invalid artifact name')
        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, 'invalid artifact encoding')
        if len(data) > MAX_ARTIFACT_BYTES:
            raise HTTPException(413, 'artifact size limit exceeded')
        digest = hashlib.sha256(data).hexdigest()
        old = s.scalar(select(RunArtifact).where(RunArtifact.run_id == run.id, RunArtifact.name == body.name))
        if old:
            if old.sha256 != digest:
                raise HTTPException(409, 'artifact name already has different content')
            return {'id': old.id, 'sha256': old.sha256}
        count, total = s.execute(select(func.count(), func.coalesce(func.sum(RunArtifact.size_bytes), 0))
                                .where(RunArtifact.run_id == run.id)).one()
        if count >= MAX_ARTIFACT_COUNT or total + len(data) > MAX_ARTIFACT_TOTAL:
            raise HTTPException(413, 'run artifact quota exceeded')
        artifact = RunArtifact(run_id=run.id, name=body.name, sha256=digest, size_bytes=len(data), data=data)
        s.add(artifact); s.commit()
        return {'id': artifact.id, 'sha256': artifact.sha256}

    @app.post('/api/v1/worker/executions/{execution_id}/complete')
    def complete(execution_id: str, body: Finish, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        run = run_for(s, execution_id, worker)
        if run.status in TERMINAL:
            if run.cleanup_confirmed and run.status == body.status:
                return {'status': run.status}
            raise HTTPException(409, 'execution already ended')
        live(run)
        if body.status == 'SUCCEEDED' and (not body.metrics or body.metrics.episode_count != s.get(Job, run.job_id).episodes):
            raise HTTPException(400, 'successful execution requires complete metrics')
        job = s.get(Job, run.job_id)
        if body.status == 'SUCCEEDED' and job.client_spec and job.client_spec['template'].get('episode_reporting'):
            from .episodes import progress
            p = progress(s, run)
            if p['completed'] != job.episodes or abs(p['success_rate'] - body.metrics.success_rate) > 1e-9:
                raise HTTPException(400, 'successful execution requires all planned episode results and consistent metrics')
        status = 'CANCELLED' if run.status == 'CANCEL_REQUESTED' else body.status
        run.result_json = body.metrics.model_dump() if status == 'SUCCEEDED' else None
        run.cleanup_confirmed = True
        transition(s, run, status, body.error)
        s.commit()
        return {'status': status}

    @app.post('/api/v1/worker/executions/{execution_id}/cleanup')
    def cleanup(execution_id: str, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        run = run_for(s, execution_id, worker)
        if run.status not in TERMINAL:
            transition(s, run, 'CANCELLED' if run.status == 'CANCEL_REQUESTED' else 'FAILED', 'worker confirmed container stopped')
        run.cleanup_confirmed = True
        s.flush()
        others = s.scalar(select(func.count()).select_from(Run).where(Run.slot_id == run.slot_id, Run.cleanup_confirmed.is_(False)))
        if not others:
            s.get(Slot, run.slot_id).quarantined = False
        s.commit()
        return {'status': run.status}
