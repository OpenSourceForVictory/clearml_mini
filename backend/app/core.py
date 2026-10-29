from __future__ import annotations
import enum
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


JSON_TYPE = JSON().with_variant(JSONB, 'postgresql')


class Status(str, enum.Enum):
    PENDING = 'PENDING'
    RESERVED = 'RESERVED'
    ENQUEUED = 'ENQUEUED'
    STARTING = 'STARTING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCEL_REQUESTED = 'CANCEL_REQUESTED'
    CANCELLED = 'CANCELLED'
    TIMED_OUT = 'TIMED_OUT'


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='user')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Pool(Base):
    __tablename__ = 'resource_pools'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    gpu_model: Mapped[str] = mapped_column(String(100), default='*')
    min_vram_gb: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    labels: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    cpu_per_job: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    ram_gb_per_job: Mapped[int] = mapped_column(Integer, default=1, server_default='1')

    allow_shared: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    max_tasks_per_gpu: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    vram_reserve_gb: Mapped[int] = mapped_column(Integer, default=2, server_default='2')
    max_gpu_utilization: Mapped[int] = mapped_column(Integer, default=90, server_default='90')


class Worker(Base):
    __tablename__ = 'workers'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    node_name: Mapped[str] = mapped_column(String(100), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1)
    cpu_capacity: Mapped[int] = mapped_column(Integer, default=4)
    ram_gb_capacity: Mapped[int] = mapped_column(Integer, default=8)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Slot(Base):
    __tablename__ = 'gpu_slots'
    __table_args__ = (UniqueConstraint('node_name', 'gpu_index'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    node_name: Mapped[str] = mapped_column(String(100))
    gpu_index: Mapped[int] = mapped_column(Integer)
    gpu_uuid: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    pool_id: Mapped[str] = mapped_column(ForeignKey('resource_pools.id'))
    queue_name: Mapped[str] = mapped_column(String(150), unique=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(150))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    worker_id: Mapped[Optional[str]] = mapped_column(ForeignKey('workers.id'), index=True)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')

    gpu_model: Mapped[Optional[str]] = mapped_column(String(100))
    vram_mb: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    client_images: Mapped[list] = mapped_column(JSON_TYPE, default=list, server_default='[]')
    protocol_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    free_vram_mb: Mapped[Optional[int]] = mapped_column(Integer)
    gpu_utilization: Mapped[Optional[int]] = mapped_column(Integer)
    disk_free_mb: Mapped[Optional[int]] = mapped_column(Integer)
    disk_min_free_mb: Mapped[Optional[int]] = mapped_column(Integer)
    disk_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    telemetry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = 'evaluation_jobs'
    __table_args__ = (UniqueConstraint('owner_id', 'idempotency_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    pool_id: Mapped[str] = mapped_column(ForeignKey('resource_pools.id'))
    title: Mapped[str] = mapped_column(String(200))
    benchmark: Mapped[str] = mapped_column(String(40))
    model_ref: Mapped[str] = mapped_column(String(500))
    episodes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=Status.PENDING.value, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(String(1000))
    batch_id: Mapped[Optional[str]] = mapped_column(ForeignKey('evaluation_batches.id'), index=True)
    case_id: Mapped[Optional[str]] = mapped_column(String(100))
    client_spec: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    gpu_mode: Mapped[str] = mapped_column(String(20), default='exclusive', server_default='exclusive')
    requested_vram_gb: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    required_gpu_model: Mapped[Optional[str]] = mapped_column(String(100))
    runs: Mapped[list[Run]] = relationship(back_populates='job', order_by='Run.attempt')
    owner: Mapped[User] = relationship()


class Run(Base):
    __tablename__ = 'evaluation_runs'
    __table_args__ = (UniqueConstraint('job_id', 'attempt'),
        Index('uq_held_run_share', 'slot_id', 'allocation_index', unique=True,
              postgresql_where=text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED') OR cleanup_confirmed = false"),
              sqlite_where=text("status IN ('RESERVED','ENQUEUED','STARTING','RUNNING','CANCEL_REQUESTED') OR cleanup_confirmed = false")),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey('evaluation_jobs.id'), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default=Status.PENDING.value, index=True)
    slot_id: Mapped[Optional[str]] = mapped_column(ForeignKey('gpu_slots.id'), index=True)
    queue_name: Mapped[Optional[str]] = mapped_column(String(150))
    clearml_task_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    reserved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reservation_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enqueued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    error: Mapped[Optional[str]] = mapped_column(String(1000))
    execution_id: Mapped[Optional[str]] = mapped_column(String(36), unique=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cleanup_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    log_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    allocation_index: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    reserved_vram_mb: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    exclusive: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    reserved_cpu: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    reserved_ram_gb: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    job: Mapped[Job] = relationship(back_populates='runs')


class RunLog(Base):
    __tablename__ = 'run_logs'
    run_id: Mapped[str] = mapped_column(ForeignKey('evaluation_runs.id'), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text)


class Event(Base):
    __tablename__ = 'job_events'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey('evaluation_jobs.id'))
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey('evaluation_runs.id'))
    old_status: Mapped[Optional[str]] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30))
    message: Mapped[Optional[str]] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SchedulerState(Base):
    __tablename__ = 'scheduler_state'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


def session_factory(url=None):
    engine = create_engine(url or os.getenv('DATABASE_URL', 'sqlite:///./eval.db'))
    return sessionmaker(engine, expire_on_commit=False)


class EvaluationSuite(Base):
    __tablename__ = 'evaluation_suites'
    __table_args__ = (UniqueConstraint('owner_id', 'name', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(100))
    template_id: Mapped[str] = mapped_column(ForeignKey('client_templates.id'))
    cases: Mapped[list] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EpisodeRecord(Base):
    __tablename__ = 'episode_records'
    run_id: Mapped[str] = mapped_column(ForeignKey('evaluation_runs.id'), primary_key=True)
    episode_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    started: Mapped[dict] = mapped_column(JSON_TYPE)
    result: Mapped[Optional[dict]] = mapped_column(JSON_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ClientTemplate(Base):
    __tablename__ = 'client_templates'
    owner_id: Mapped[Optional[str]] = mapped_column(ForeignKey('users.id'), index=True)
    __table_args__ = (UniqueConstraint('name', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    image: Mapped[str] = mapped_column(String(500))
    command: Mapped[list] = mapped_column(JSON_TYPE)
    protocols: Mapped[list] = mapped_column(JSON_TYPE)
    episode_reporting: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    endpoint_exclusive: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RunArtifact(Base):
    __tablename__ = 'run_artifacts'
    __table_args__ = (UniqueConstraint('run_id', 'name'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey('evaluation_runs.id'), index=True)
    name: Mapped[str] = mapped_column(String(180))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationBatch(Base):
    __tablename__ = 'evaluation_batches'
    __table_args__ = (UniqueConstraint('owner_id', 'idempotency_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    suite_id: Mapped[Optional[str]] = mapped_column(ForeignKey('evaluation_suites.id'))
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    title: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ClientBuild(Base):
    __tablename__ = 'client_builds'
    __table_args__ = (UniqueConstraint('owner_id', 'name', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(100))
    entrypoint: Mapped[str] = mapped_column(String(200))
    source: Mapped[dict] = mapped_column(JSON_TYPE, deferred=True)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default='PENDING', index=True)
    claim_hash: Mapped[Optional[str]] = mapped_column(String(64))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    log: Mapped[str] = mapped_column(Text, default='')
    image: Mapped[Optional[str]] = mapped_column(String(500))
    template_id: Mapped[Optional[str]] = mapped_column(ForeignKey('client_templates.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
