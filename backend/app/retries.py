"""Retry mutation shared by single and bulk endpoints; caller holds scheduler lock."""
import os
from fastapi import HTTPException
from sqlalchemy import select, func
from .core import Job, Run, User, Event
from .scheduling import TERMINAL


def retry_job(s, job, expected_run_id=None, allowed=('FAILED', 'CANCELLED', 'TIMED_OUT')):
    if expected_run_id is not None and job.runs[-1].id != expected_run_id:
        raise HTTPException(409, 'attempt changed')
    if job.status not in allowed:
        raise HTTPException(409, 'job not retryable')
    if not job.runs[-1].cleanup_confirmed:
        raise HTTPException(409, 'previous execution cleanup is not confirmed')
    s.execute(select(User.id).where(User.id == job.owner_id).with_for_update())
    condition = Job.status.not_in(TERMINAL) if job.batch_id else Job.status == 'PENDING'
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == job.owner_id, condition))
    limit = int(os.getenv('MAX_BATCH_BACKLOG_PER_USER', '10000')) if job.batch_id else int(os.getenv('MAX_PENDING_PER_USER', '20'))
    if pending >= limit:
        raise HTTPException(429, 'pending limit')
    old = job.status
    job.status = 'PENDING'
    job.started_at = job.finished_at = job.queued_at = job.error = None
    run = Run(job_id=job.id, attempt=job.runs[-1].attempt + 1)
    s.add(run)
    s.flush()
    s.add(Event(job_id=job.id, run_id=run.id, old_status=old, new_status='PENDING'))
    return run
