"""Authenticated worker protocol and administrator provisioning."""
import hashlib
import base64
import binascii
import os
import secrets
from . import log_policy
from datetime import timedelta
from typing import Literal, Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict, field_validator
from argon2 import PasswordHasher
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from . import storage as storage_protocol
from . import memberships, image_runtime, endpoints, host_environments, worker_upgrades
from .core import EvaluationBatch, Job, Pool, Run, RunArtifact, RunLog, Slot, User, Worker, now
from .scheduling import ACTIVE, TERMINAL, lock, online, seconds, transition, utc, execution_timeout
from .freshness import RESOURCE_SAMPLE_SECONDS, recent
from runner.cs_contract import MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL, MAX_ARTIFACT_COUNT, artifact_name

MAX_LOG_BYTES = 262144


class Register(BaseModel):
    accelerator_type:Literal['gpu','npu']='gpu'
    accelerator_backends:list[Literal['gpu:docker:exclusive','gpu:docker:shared','gpu:host:exclusive','gpu:host:shared','npu:docker:exclusive','npu:docker:shared','npu:host:exclusive','npu:host:shared']]=Field(default_factory=list,max_length=8)
    pool_id: str | None = None
    gpu_index: int = Field(ge=0, le=1023)
    gpu_uuid: str = Field(min_length=1, max_length=100)
    gpu_model: str = Field(min_length=1, max_length=100)
    vram_gb: int = Field(ge=0, le=10000)
    vram_mb: Optional[int] = Field(default=None, ge=0, le=10240000)
    software: worker_upgrades.Software | None = None
    protocol_version: Literal[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18] = 1
    client_images: list[str] = Field(default_factory=list, max_length=100)
    execution_modes: list[Literal['docker','host']] = Field(default=['docker'],min_length=1,max_length=2)


class Telemetry(BaseModel):
    free_vram_mb: int = Field(ge=0, le=10240000)
    gpu_utilization: int = Field(ge=0, le=100)


class DiskTelemetry(BaseModel):
    free_mb: int = Field(ge=0, le=2147483647)
    min_free_mb: int = Field(ge=0, le=2147483647)


class Claim(BaseModel):
    known_execution_ids: list[str] = Field(default_factory=list, max_length=1024)


class Heartbeat(BaseModel):
    software: worker_upgrades.Software | None = None
    slot_id: str
    execution_id: Optional[str] = None
    started: bool = True
    telemetry: Optional[Telemetry] = None
    telemetry_failed: bool = False
    disk: Optional[DiskTelemetry] = None
    storage: Optional[storage_protocol.StorageReport] = None
    runtime: Optional[image_runtime.Report] = None
    host: Optional[host_environments.Report] = None


class Metrics(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    success_rate: float = Field(ge=0, le=1)
    episode_count: int = Field(ge=1, le=10000)
    runtime: float = Field(ge=0)


from .recovery_control import ProcessIdentity


class RecoveryProof(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=36)
    identity: ProcessIdentity


class Finish(BaseModel):
    recovery: RecoveryProof | None = None
    log_gap: bool = False
    exit_code: int | None = Field(default=None, strict=True, ge=-255, le=255)
    status: Literal['SUCCEEDED', 'FAILED', 'CANCELLED']
    metrics: Optional[Metrics] = None
    error: Optional[str] = Field(default=None, max_length=1000)


def validate_finish(s, run, body):
    job = s.get(Job, run.job_id)
    command=(job.client_spec or {}).get('template',{}).get('task_type')=='command' and job.benchmark!='inference-service'
    if command and body.metrics:raise HTTPException(400,'ordinary commands do not report evaluation metrics')
    if body.status == 'SUCCEEDED' and not command and (not body.metrics or body.metrics.episode_count != job.episodes):
        raise HTTPException(400, 'successful execution requires complete metrics')
    job = s.get(Job, run.job_id)
    if body.status == 'SUCCEEDED' and job.client_spec and job.client_spec['template'].get('episode_reporting'):
        from .episodes import progress
        p = progress(s, run)
        if p['completed'] != job.episodes or (not command and abs(p['success_rate'] - body.metrics.success_rate) > 1e-9):
            raise HTTPException(400, 'successful execution requires all planned episode results and consistent metrics')


class TaskResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    success: bool = Field(strict=True)
    metrics: dict = Field(default_factory=dict)
    summary: str | None = Field(default=None, max_length=500)


class LogBody(BaseModel):
    sequence: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=8192)


class ArtifactUpload(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(max_length=4*((MAX_ARTIFACT_BYTES+2)//3))


class WorkerCreate(BaseModel):
    node_name: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    roles: list[Literal['client', 'server']] = Field(default=['client'], min_length=1, max_length=2)
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
    cpu_per_job: int = Field(default=1, ge=0, le=10000)
    ram_gb_per_job: int = Field(default=1, ge=0, le=100000)


class Enabled(BaseModel):
    model_config = ConfigDict(extra='forbid')
    roles: list[Literal['client', 'server']] | None = Field(default=None, min_length=1, max_length=2)
    expected_revision: int | None = Field(default=None, ge=0)
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
        w = s.scalar(select(Worker).where(Worker.token_hash == token_hash(token.credentials), Worker.revoked_at.is_(None), Worker.deleted_at.is_(None)))
        if not w:
            raise HTTPException(401, 'invalid worker credential')
        return w

    from . import recovery_records
    recovery_records.install(app, db, worker_auth)
    from . import recovery_control
    recovery_control.install(app, db, worker_auth)
    from . import recovery_claims
    recovery_claims.install(app, db, worker_auth)
    from . import recovery_launch
    recovery_launch.install(app, db, worker_auth)

    def admin(user=Depends(current)):
        if user.role != 'admin':
            raise HTTPException(403, 'admin required')
        return user

    from . import administration
    administration.install(app, db, admin)
    from . import enrollment
    enrollment.install(app, db, admin)
    from . import ssh_onboarding
    ssh_onboarding.install(app, db, admin)
    from . import templates
    templates.install(app, db, admin)
    from . import template_versions
    template_versions.install(app, db, current, admin)
    host_environments.install(app, db, admin)
    from . import services
    services.install(app, db, admin, worker_auth)
    from . import service_network
    service_network.install(app,db,admin)
    worker_upgrades.install(app, db, admin, worker_auth)
    from . import node_agents
    node_agents.install(app, db, admin, worker_auth)
    storage_protocol.install(app, db, admin)
    image_runtime.install(app, db, admin, current)
    memberships.install(app, db, admin)
    from .retention import install as install_retention
    install_retention(app, db, admin, current, worker_auth)
    from .process_logs import install as install_process_logs
    install_process_logs(app, db, current, worker_auth)
    from .log_policy import install as install_log_policy
    install_log_policy(app, db, admin, worker_auth)
    from .shared_artifacts import install as install_shared_artifacts
    install_shared_artifacts(app, db, current, worker_auth)

    @app.get("/api/v1/worker/self")
    def worker_self(worker=Depends(worker_auth), s: Session = Depends(db)):
        return {"id": worker.id, "node_name": worker.node_name, "enabled":worker.enabled, "enrollment_state":(worker.connection or {}).get("enrollment",{}).get("state"), "release_id":(worker.connection or {}).get("enrollment",{}).get("release_id"), "gpu_indices": [x.gpu_index for x in s.scalars(select(Slot).where(Slot.worker_id==worker.id))], "capabilities": ["poolless_registration", "managed_membership", "storage_v4", "runtime_v5", "retention_v6"]}

    def slot_for(s, slot_id, worker):
        slot = s.get(Slot, slot_id)
        if not slot or slot.worker_id != worker.id:
            raise HTTPException(404, 'slot not found')
        return slot

    def run_for(s, execution_id, worker):
        # Execution identity is immutable. NO KEY UPDATE still serializes state
        # changes, while allowing log chunk FK checks to run during renewal.
        run = s.scalar(select(Run).where(Run.execution_id == execution_id)
                       .with_for_update(key_share=True))
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
        if s.scalar(select(Worker).where(Worker.node_name == body.node_name, Worker.deleted_at.is_(None))):
            raise HTTPException(409, 'node already provisioned')
        token = 'wk_' + secrets.token_urlsafe(32)
        worker = Worker(token_hash=token_hash(token), **body.model_dump())
        s.add(worker)
        s.commit()
        return {'id': worker.id, 'token': token, 'node_name': worker.node_name}

    @app.get('/api/v1/admin/nodes')
    def nodes(user=Depends(admin), s: Session = Depends(db)):
        return administration.node_views(s, list(s.scalars(select(Worker).where(Worker.deleted_at.is_(None)).order_by(Worker.node_name))))

    @app.post('/api/v1/admin/nodes/{worker_id}/rotate-token')
    def rotate(worker_id: str, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        w = s.get(Worker, worker_id)
        if not w or w.revoked_at:
            raise HTTPException(404, 'node not found')
        token = 'wk_' + secrets.token_urlsafe(32)
        w.token_hash = token_hash(token)
        s.commit()
        return {'id': w.id, 'node_name': w.node_name, 'token': token}

    @app.patch('/api/v1/admin/nodes/{worker_id}')
    def enable_node(worker_id: str, body: Enabled, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        w = s.get(Worker, worker_id)
        if not w or w.revoked_at:
            raise HTTPException(404, 'node not found')
        return administration.update_node(s, w, body, user)

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
    def update_pool(pool_id: str, body: administration.PoolUpdate, user=Depends(admin), s: Session = Depends(db)):
        return administration.update_pool(s, pool_id, body, user)

    @app.post('/api/v1/worker/register')
    def register(body: Register, worker=Depends(worker_auth), s: Session = Depends(db)):
        lock(s)
        if (body.gpu_model == 'virtual') != body.gpu_uuid.startswith('mock:'):
            raise HTTPException(400, 'virtual and physical device identities must be distinct')
        if body.accelerator_type=='npu' and (body.protocol_version<11 or not body.gpu_uuid.startswith('ascend:')):
            raise HTTPException(400,'NPU registration requires protocol 11 and stable Ascend identity')
        existing = s.scalar(select(Slot).where(Slot.gpu_uuid == body.gpu_uuid, Slot.deleted_at.is_(None)))
        indexed = s.scalar(select(Slot).where(Slot.node_name == worker.node_name, Slot.gpu_index == body.gpu_index, Slot.deleted_at.is_(None)))
        if existing and (existing.worker_id != worker.id or existing.gpu_index != body.gpu_index):
            raise HTTPException(409, 'GPU UUID already registered to another slot')
        if indexed and indexed.gpu_uuid != body.gpu_uuid:
            raise HTTPException(409, 'GPU index already registered to another UUID')
        slot = existing or indexed
        managed = slot is not None and slot.membership_revision > 0
        if not managed and body.pool_id is not None:
            pool = s.get(Pool, body.pool_id)
            if not pool or not pool.enabled or pool.min_vram_gb * 1024 > (body.vram_mb if body.vram_mb is not None else body.vram_gb * 1024):
                raise HTTPException(400, 'device does not match pool requirements')
            if (pool.gpu_model == 'virtual') != body.gpu_uuid.startswith('mock:'):
                raise HTTPException(400, 'virtual and physical device identities must be distinct')
        if slot and not managed:
            if slot.pool_id != body.pool_id:
                held = s.scalar(select(Run.id).where(Run.slot_id == slot.id,
                    or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False))).limit(1))
                if held:
                    raise HTTPException(409, 'cannot move a slot with held executions')
            slot.pool_id = slot.desired_pool_id = body.pool_id
            if body.pool_id is None:
                slot.membership_revision = slot.membership_applied_revision = 1
        if not slot and worker.upgrade_state in worker_upgrades.BLOCKING:
            raise HTTPException(409,'cannot add a GPU during a node upgrade')
        if not slot and not worker.enabled and not enrollment.accepts_new_gpu(worker,body.gpu_index,body.gpu_uuid):
            raise HTTPException(409, 'disabled node cannot register new GPUs')
        if not slot:
            revision = 1 if body.pool_id is None else 0
            slot = Slot(node_name=worker.node_name, gpu_index=body.gpu_index, gpu_uuid=body.gpu_uuid,
                        pool_id=body.pool_id, desired_pool_id=body.pool_id,
                        membership_revision=revision, membership_applied_revision=revision,
                        worker_id=worker.id, accelerator_type=body.accelerator_type, queue_name=f'{worker.id}-{body.gpu_index}', agent_id=worker.id)
            s.add(slot)
        if slot.accelerator_type and slot.accelerator_type!=body.accelerator_type:raise HTTPException(409,'cannot change registered accelerator type')
        slot.accelerator_type=body.accelerator_type
        slot.accelerator_backends=body.accelerator_backends if body.protocol_version>=11 else []
        slot.gpu_model = body.gpu_model
        slot.vram_mb = body.vram_mb if body.vram_mb is not None else body.vram_gb * 1024
        slot.software = log_policy.merge_software(slot.software, body.software.model_dump() if body.software else None)
        if body.software and 'probe_references' not in body.software.model_fields_set:
            slot.software.pop('probe_references',None)
        slot.protocol_version = body.protocol_version
        slot.execution_modes = list(set(body.execution_modes)) if body.protocol_version >= 7 else ['docker']
        slot.client_images = body.client_images if body.protocol_version >= 3 else []
        slot.storage_reported_at = None
        slot.runtime_reported_at = None
        slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        slot.last_seen_at = slot.heartbeat_at = now()
        s.commit()
        return {'resource_source': 'agent' if node_agents.resource_source(worker) else 'worker', 'slot_id': slot.id, 'quarantined': slot.quarantined, 'pool_id': slot.pool_id, **memberships.view(s, slot)}

    @app.post('/api/v1/worker/heartbeat')
    def heartbeat(body: Heartbeat, worker=Depends(worker_auth), s: Session = Depends(db)):
        # Pure renewal does not change resource ownership. Only the initial
        # STARTING transition participates in global scheduling serialization.
        if body.execution_id and s.scalar(select(Run.status).where(
                Run.execution_id == body.execution_id)) == 'STARTING':
            from .scheduling import fast_lock
            fast_lock(s)
        # Execution reports never modify Worker configuration JSON. Lock Run
        # before Slot, matching completion/reclamation; an ordinary heartbeat
        # still locks Worker before merging configuration acknowledgements.
        run = run_for(s, body.execution_id, worker) if body.execution_id else None
        s.refresh(worker, with_for_update=not bool(body.execution_id))
        slot = slot_for(s, body.slot_id, worker)
        s.refresh(slot, with_for_update=True)
        cancel = False
        if body.execution_id:
            if run.slot_id != slot.id:
                raise HTTPException(409, 'execution belongs to another slot')
            live(run)
            if run.status == 'STARTING' and body.started:
                run.started_at = s.get(Job, run.job_id).started_at = now()
                transition(s, run, 'RUNNING')
            run.lease_expires_at = now() + timedelta(seconds=seconds('LEASE_SECONDS', 30))
            cancel = run.status == 'CANCEL_REQUESTED'
        if not body.execution_id and body.software is not None:
            slot.software = log_policy.merge_software(slot.software, body.software.model_dump())
        if not body.execution_id and body.storage is not None:
            storage_protocol.acknowledge(s, worker, slot, body.storage)
        if not body.execution_id and body.runtime is not None:
            image_runtime.acknowledge(s, worker, slot, body.runtime)
        if body.host is not None and not body.execution_id:
            host_environments.acknowledge(s, worker, slot, body.host)
        if body.disk is not None and not body.execution_id:
            slot.disk_free_mb = body.disk.free_mb
            slot.disk_min_free_mb = body.disk.min_free_mb
            slot.disk_at = now()
        if node_agents.resource_source(worker):
            pass  # Agent is authoritative, including failed/stale samples.
        elif body.telemetry_failed:
            slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        elif body.telemetry is not None:
            if body.telemetry.free_vram_mb > slot.vram_mb:
                raise HTTPException(400, 'free VRAM exceeds device capacity')
            slot.free_vram_mb = body.telemetry.free_vram_mb
            slot.gpu_utilization = body.telemetry.gpu_utilization
            slot.telemetry_at = now()
        slot.last_seen_at = slot.heartbeat_at = now()
        s.commit()
        return {'resource_source': 'agent' if node_agents.resource_source(worker) else 'worker', 'lease_seconds': seconds('LEASE_SECONDS', 30), 'cancel': cancel,
                'host': host_environments.desired(worker), 'runtime': image_runtime.desired(worker), 'storage': storage_protocol.desired(worker, slot), 'storage_busy': storage_protocol.held(s, slot)}

    @app.post('/api/v1/worker/slots/{slot_id}/claim')
    def claim(slot_id: str, body: Optional[Claim] = None, worker=Depends(worker_auth), s: Session = Depends(db)):
        slot = slot_for(s, slot_id, worker)
        known = body.known_execution_ids if body else []
        # Idle polling only reads the assigned slot. A reservation committed
        # after this check is seen on the next poll; no state is changed here.
        candidate = s.scalar(select(Run.id).where(
            Run.slot_id == slot.id,
            or_(
                (Run.cleanup_confirmed.is_(False) & Run.status.in_(TERMINAL)),
                (Run.status.in_(ACTIVE) & Run.execution_id.not_in(known)),
            ),
        ).limit(1))
        if candidate is None:
            return {'task': None, 'cleanup': []}
        if os.getenv('DB_BACKPRESSURE_ENABLED', '0') == '1':
            if not lock(s, wait=False):
                return {'task': None, 'cleanup': []}
        else:
            lock(s)
        # Read committed sees changes made while we waited for the lock.
        s.refresh(slot)
        cleanup = list(s.scalars(select(Run.execution_id).where(Run.slot_id == slot.id, Run.cleanup_confirmed.is_(False), Run.status.in_(TERMINAL))))
        if cleanup:
            return {'task': None, 'cleanup': cleanup}
        run = s.scalar(select(Run).where(Run.slot_id == slot.id, Run.status.in_(ACTIVE),
            or_(Run.waiting_service.is_(False),Run.status!='RESERVED'),
            Run.execution_id.not_in(known)).order_by(Run.reserved_at, Run.id).limit(1))
        if not run or not online(slot) or slot.quarantined:
            return {'task': None, 'cleanup': []}
        fresh_artifact_plan=run.status=='RESERVED'
        if run.status == 'RESERVED':
            s.refresh(worker)
            if not worker.enabled or not slot.enabled or worker.upgrade_state in worker_upgrades.BLOCKING or not node_agents.admission(worker):
                return {'task': None, 'cleanup': []}
            job = s.get(Job, run.job_id)
            from .service_dependencies import binding_ready
            if not binding_ready(s,run):return {'task':None,'cleanup':[]}
            run.waiting_service=False
            if job.batch_id and s.get(EvaluationBatch, job.batch_id).scheduling_paused:
                return {'task': None, 'cleanup': []}
            s.refresh(worker)
            from .scheduling import workload_codes, runtime_mismatch
            if runtime_mismatch(slot, job):return {'task':None,'cleanup':[]}
            if workload_codes(worker, slot, s.get(Job, run.job_id)):
                return {'task': None, 'cleanup': []}
            if image_runtime.blockers(worker, slot, s.get(Job, run.job_id).client_spec, now()):
                return {'task': None, 'cleanup': []}
            if not memberships.ready(slot) or not storage_protocol.ready(worker, slot, now()):
                return {'task': None, 'cleanup': []}
            if slot.disk_min_free_mb is not None and (slot.disk_free_mb is None or slot.disk_free_mb < slot.disk_min_free_mb
                    or not recent(slot.disk_at, now(), RESOURCE_SAMPLE_SECONDS)):
                return {'task': None, 'cleanup': []}
            if node_agents.resource_source(worker):
                from types import SimpleNamespace
                from .scheduling import telemetry_fresh
                requested = SimpleNamespace(cpu_per_job=run.reserved_cpu, ram_gb_per_job=run.reserved_ram_gb)
                if (node_agents.host_codes(worker, requested, 0, 0)
                        or (job.requested_vram_gb and not telemetry_fresh(slot, now()))):
                    return {'task': None, 'cleanup': []}
            if utc(run.reservation_expires_at) <= now():
                return {'task': None, 'cleanup': []}
            run.enqueued_at = now()
            run.lease_expires_at = now() + timedelta(seconds=seconds('LEASE_SECONDS', 30))
            run.cleanup_confirmed = False
            transition(s, run, 'STARTING')
        else:
            live(run)
        job = s.get(Job, run.job_id)
        pool = s.get(Pool, job.pool_id)
        task = {'runtime_revision': (slot.runtime_report or {}).get('applied_revision', 0), 'storage_revision': slot.storage_applied_revision, 'run_id': run.id, 'execution_id': run.execution_id, 'job_id': job.id, 'batch_id': job.batch_id,
                'benchmark': job.benchmark, 'model_ref': job.model_ref, 'episodes': job.episodes,
                'status': run.status, 'image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:dev'),
                'accelerator_type':slot.accelerator_type,'device_index':slot.gpu_index,
                'gpu_uuid': None if pool.gpu_model == 'virtual' else slot.gpu_uuid,
                'cpus': run.reserved_cpu, 'ram_gb': run.reserved_ram_gb,
                'gpu_mode': 'exclusive' if run.exclusive else 'shared',
                'requested_vram_mb': job.requested_vram_gb * 1024,
                'vram_reserve_mb': pool.vram_reserve_gb * 1024,
                'timeout_seconds': execution_timeout(job),
                'lease_seconds': seconds('LEASE_SECONDS', 30)}
        if job.service_instance_id:
            task['service_instance_id'] = job.service_instance_id
        if job.client_spec:
            resources=job.client_spec.get('resources',{})
            task['reserved_cpus']=run.reserved_cpu
            task['reserved_ram_gb']=run.reserved_ram_gb
            if resources.get('cpu_unlimited'):task['cpus']=0
            if resources.get('memory_unlimited'):task['ram_gb']=0
            task['client_spec'] = endpoints.input_spec(run, job.client_spec)
            task['image'] = job.client_spec['template']['image']
            if job.client_spec['template'].get('execution_mode') == 'host':
                task['host_revision'] = worker.host_revision
                task['host_profile'] = worker.host_environments[job.client_spec['template']['host_runtime']['environment_name']]
        template = (job.client_spec or {}).get('template', {})
        if ('bounded_logs_v2' in (slot.software or {}).get('capabilities', [])
                and (template.get('execution_mode') == 'host' or template.get('runtime'))):
            task['log_protocol'] = 2
            if 'process_logs_v1' in (slot.software or {}).get('capabilities',[]):task['process_logs_v1']=True
        if ('episode_timing_v1' in (slot.software or {}).get('capabilities', [])
                and (template.get('execution_mode') == 'host' or template.get('runtime'))):
            task['episode_timing_v1'] = True
        if task.get('process_logs_v1'):
            from .log_policy import snapshot as log_snapshot
            value=log_snapshot(s,run,slot)
            if value is not None:task['process_log_policy']=value
        from .shared_artifacts import plan as artifact_plan
        task.update(artifact_plan(s,run,worker,slot,job,fresh_artifact_plan))
        from .result_directories import plan as browse_plan
        browse_plan(s,run,worker,slot,job,fresh_artifact_plan)
        if job.benchmark == 'cs-client':
            from .shared_storage import mappings, allows_node
            if any(allows_node(m, worker.node_name) for m in mappings()):
                task['artifact_transport'] = 'shared'
        s.commit()
        return {'task': task, 'cleanup': []}

    from . import execution_logs
    execution_logs.install(app, db, worker_auth)

    @app.post('/api/v1/worker/executions/{execution_id}/logs')
    def logs(execution_id: str, body: LogBody, worker=Depends(worker_auth), s: Session = Depends(db)):
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
        run = run_for(s, execution_id, worker)
        live(run)
        record(s, run, body)
        s.commit()
        return {'accepted': True}

    @app.post('/api/v1/worker/executions/{execution_id}/artifacts')
    def upload_artifact(execution_id: str, body: ArtifactUpload, worker=Depends(worker_auth), s: Session = Depends(db)):
        if not artifact_name(body.name):
            raise HTTPException(400, 'invalid artifact name')
        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(400, 'invalid artifact encoding')
        if len(data) > MAX_ARTIFACT_BYTES:
            raise HTTPException(413, 'artifact size limit exceeded')
        digest = hashlib.sha256(data).hexdigest()
        run = run_for(s, execution_id, worker)
        live(run)
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
        if body.recovery is not None:
            from .recovery_finish import finish
            result = finish(s, worker_id=worker.id, execution_id=execution_id, body=body)
            s.commit()
            return result
        lock(s)
        run = run_for(s, execution_id, worker)
        from .core import ExecutionRecovery
        if s.get(ExecutionRecovery, execution_id):
            raise HTTPException(409, 'recovery execution requires identity proof')
        from .execution_logs import terminal_log_lock
        terminal_log_lock(s, run.id)
        if run.status in TERMINAL:
            if run.cleanup_confirmed and run.status == body.status:
                return {'status': run.status}
            raise HTTPException(409, 'execution already ended')
        live(run)
        validate_finish(s, run, body)
        status = 'CANCELLED' if run.status == 'CANCEL_REQUESTED' else body.status
        if body.log_gap:
            from .core import RunLogState
            logs = s.get(RunLogState, run.id)
            if not logs:
                logs = RunLogState(run_id=run.id); s.add(logs)
            logs.truncated = True
        if status == 'SUCCEEDED' and body.metrics:
            run.result_json = body.metrics.model_dump()
        elif not (isinstance(run.result_json, dict) and run.result_json.get('kind') == 'task'):
            run.result_json = None
        run.exit_code = body.exit_code
        run.cleanup_confirmed = True
        transition(s, run, status, body.error)
        memberships.reconcile(s)
        s.commit()
        return {'status': status}

    @app.post('/api/v1/worker/executions/{execution_id}/task-result')
    def task_result(execution_id: str, body: TaskResult, worker=Depends(worker_auth), s: Session = Depends(db)):
        run = run_for(s, execution_id, worker)
        live(run)
        job = s.get(Job, run.job_id)
        template = (job.client_spec or {}).get('template', {})
        if template.get('integration_mode') != 'sdk-task-result' or template.get('task_type') != 'command':
            raise HTTPException(400, 'execution does not use task-result SDK reporting')
        from runner.task_result_contract import validate
        try:
            result = {'kind': 'task', **validate(body.model_dump(exclude_none=True))}
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(400, str(exc)) from None
        if run.result_json is not None:
            if run.result_json != result:
                raise HTTPException(409, 'conflicting task result for execution')
            return {'accepted': True, 'duplicate': True}
        run.result_json = result
        s.commit()
        return {'accepted': True, 'duplicate': False}

    @app.post('/api/v1/worker/executions/{execution_id}/cleanup')
    def cleanup(execution_id: str, body: RecoveryProof | None = None, worker=Depends(worker_auth), s: Session = Depends(db)):
        if body is not None:
            from .recovery_finish import cleanup as recovery_cleanup
            result = recovery_cleanup(s, worker_id=worker.id, execution_id=execution_id, proof=body)
            s.commit()
            return result
        lock(s)
        run = run_for(s, execution_id, worker)
        from .core import ExecutionRecovery
        if s.get(ExecutionRecovery, execution_id):
            raise HTTPException(409, 'recovery execution requires identity proof')
        from .execution_logs import terminal_log_lock
        terminal_log_lock(s, run.id)
        if run.status not in TERMINAL:
            transition(s, run, 'CANCELLED' if run.status == 'CANCEL_REQUESTED' else 'FAILED', 'worker confirmed container stopped')
        run.cleanup_confirmed = True
        s.flush()
        others = s.scalar(select(func.count()).select_from(Run).where(Run.slot_id == run.slot_id, Run.cleanup_confirmed.is_(False)))
        if not others:
            s.get(Slot, run.slot_id).quarantined = False
        memberships.reconcile(s)
        s.commit()
        return {'status': run.status}
