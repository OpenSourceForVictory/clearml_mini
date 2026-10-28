from __future__ import annotations
import enum
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, create_engine
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
    gpu_model: Mapped[str] = mapped_column(String(100))
    min_vram_gb: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    labels: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)


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
    runs: Mapped[list[Run]] = relationship(back_populates='job', order_by='Run.attempt')


class Run(Base):
    __tablename__ = 'evaluation_runs'
    __table_args__ = (UniqueConstraint('job_id', 'attempt'),)
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
    job: Mapped[Job] = relationship(back_populates='runs')


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
