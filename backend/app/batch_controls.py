"""Batch controls serialize with scheduling, claims and retries."""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .core import now
from .scheduling import lock, transition, TERMINAL


class Control(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['pause', 'resume', 'cancel']
    expected_revision: int = Field(ge=0)


def cancel_job(s, job):
    if job.status in TERMINAL:
        raise HTTPException(409, 'terminal job')
    if job.status != 'CANCEL_REQUESTED':
        transition(s, job.runs[-1], 'CANCELLED' if job.status in ('PENDING', 'RESERVED') else 'CANCEL_REQUESTED')
    return job.status


def withdraw(s, job):
    run = job.runs[-1]
    transition(s, run, 'PENDING', 'batch scheduling paused before claim')
    run.slot_id = run.queue_name = run.execution_id = None
    run.reserved_at = run.reservation_expires_at = None
    run.allocation_index = run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = 0
    job.queued_at = None


def install(app, db, current):
    from .monitoring import owned, batch_jobs

    @app.post('/api/v1/batches/{batch_id}/control')
    def control(batch_id: str, body: Control, user=Depends(current), s=Depends(db)):
        lock(s)
        batch = owned(s, batch_id, user)
        if batch.control_revision != body.expected_revision:
            raise HTTPException(409, 'batch control changed; refresh before retrying')
        jobs = batch_jobs(s, batch)
        counts = dict(withdrawn=0, cancelled=0, cancel_requested=0, already_cancelling=0, terminal_unchanged=0)
        batch.scheduling_paused = body.action != 'resume'
        batch.control_revision += 1
        batch.control_updated_at = now()
        for job in jobs:
            if body.action == 'pause' and job.status == 'RESERVED':
                withdraw(s, job)
                counts['withdrawn'] += 1
            elif body.action == 'cancel':
                if job.status in TERMINAL:
                    counts['terminal_unchanged'] += 1
                elif job.status == 'CANCEL_REQUESTED':
                    counts['already_cancelling'] += 1
                else:
                    status = cancel_job(s, job)
                    counts['cancelled' if status == 'CANCELLED' else 'cancel_requested'] += 1
        s.commit()
        return dict(action=body.action, scheduling_paused=batch.scheduling_paused,
                    control_revision=batch.control_revision, control_updated_at=batch.control_updated_at,
                    counts=counts)
