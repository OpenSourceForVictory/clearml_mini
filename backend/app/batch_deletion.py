"""Logical history deletion; never discards execution/resource ownership records."""
from fastapi import HTTPException
from sqlalchemy import select
from .core import EvaluationBatch, Job, Run, ServiceInstance, now
from .scheduling import lock, TERMINAL


def assert_live_batch(s, batch_id):
    batch = s.get(EvaluationBatch, batch_id) if batch_id else None
    if batch and batch.deleted_at:
        raise HTTPException(409, 'batch has been deleted')


def assert_live_job(s, job):
    assert_live_batch(s, job.batch_id)
    if job.service_instance_id:
        item = s.get(ServiceInstance, job.service_instance_id)
        if item:
            assert_live_batch(s, item.orchestration_batch_id)


def remove(s, identity, user):
    # Same transaction lock as claims, retries and service lifecycle changes.
    lock(s)
    batch = s.get(EvaluationBatch, identity)
    if not batch or (batch.owner_id != user.id and user.role != 'admin'):
        raise HTTPException(404, 'batch not found')
    if batch.deleted_at:
        return {'id': identity, 'deleted': True}
    services = list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id == identity)))
    service_ids = [item.id for item in services]
    jobs = list(s.scalars(select(Job).where((Job.batch_id == identity) | Job.service_instance_id.in_(service_ids))))
    if any(j.status not in TERMINAL for j in jobs) or s.scalar(select(Run.id).where(
            Run.job_id.in_([j.id for j in jobs]), Run.cleanup_confirmed.is_(False)).limit(1)):
        raise HTTPException(409, 'batch tasks must be terminal and cleaned before deletion')
    if any(item.desired_running for item in services):
        raise HTTPException(409, 'batch services must be stopped and cleaned before deletion')
    batch.deleted_at = now()
    batch.scheduling_paused = True
    batch.control_revision += 1
    batch.control_updated_at = batch.deleted_at
    from .administration import audit
    audit(s, user, 'batch.delete', identity)
    return {'id': identity, 'deleted': True}
