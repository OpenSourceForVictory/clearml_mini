"""Batch deletion with a durable tombstone and removal of batch-owned work."""
from fastapi import HTTPException
from sqlalchemy import select
from .core import (
    DataCleanupItem,
    EpisodeRecord,
    EvaluationBatch,
    EvaluationOrchestration,
    Event,
    ExecutionLogPolicy, ExecutionData, ExecutionLogCatalog, LogReadRequest, LogReadSubscriber,
    Job,
    Run,
    RunArtifact, SharedArtifactRoot, ExecutionBrowseRoot,
    RunLog,
    RunLogChunk,
    RunLogState,
    ServiceInstance,
    now,
)
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

    # Keep the batch row as a tombstone so idempotency keys and deleted-state
    # guards remain effective, but remove all task-owned execution data.  The
    # explicit order is required on PostgreSQL because these tables do not use
    # database-level ON DELETE CASCADE.
    job_ids = [item.id for item in jobs]
    run_ids = list(s.scalars(select(Run.id).where(Run.job_id.in_(job_ids)).order_by(Run.id).with_for_update())) if job_ids else []
    execution_ids = list(s.scalars(select(ExecutionData.id).where(ExecutionData.run_id.in_(run_ids)))) if run_ids else []
    read_ids=list(s.scalars(select(LogReadRequest.id).where(LogReadRequest.run_id.in_(run_ids)))) if run_ids else []
    for model, column, values in (
        (LogReadSubscriber, LogReadSubscriber.request_id, read_ids),
        (LogReadRequest, LogReadRequest.run_id, run_ids),
        (ExecutionLogCatalog, ExecutionLogCatalog.run_id, run_ids),
        (DataCleanupItem, DataCleanupItem.data_id, execution_ids),
        (ExecutionData, ExecutionData.id, execution_ids),
        (RunLogChunk, RunLogChunk.run_id, run_ids),
        (RunLogState, RunLogState.run_id, run_ids),
        (RunLog, RunLog.run_id, run_ids),
        (EpisodeRecord, EpisodeRecord.run_id, run_ids),
        (RunArtifact, RunArtifact.run_id, run_ids),
        (SharedArtifactRoot, SharedArtifactRoot.run_id, run_ids),
        (ExecutionBrowseRoot, ExecutionBrowseRoot.run_id, run_ids),
        (ExecutionLogPolicy, ExecutionLogPolicy.run_id, run_ids),
        (Event, Event.job_id, job_ids),
        (Run, Run.id, run_ids),
        (Job, Job.id, job_ids),
    ):
        if values:
            for row in s.scalars(select(model).where(column.in_(values))):
                s.delete(row)
            # Marking rows deleted does not order SQL across unrelated ORM
            # mappers. Flush each dependency layer before deleting its parents;
            # notably Job -> ServiceInstance must not share an unordered flush.
            # This remains one transaction, so any later failure rolls it back.
            s.flush()
    for item in services:
        s.delete(item)
    orchestration = s.get(EvaluationOrchestration, identity)
    if orchestration:
        s.delete(orchestration)
    batch.deleted_at = now()
    batch.scheduling_paused = True
    batch.control_revision += 1
    batch.control_updated_at = batch.deleted_at
    from .administration import audit
    audit(s, user, 'batch.delete', identity, {
        'job_count': len(job_ids),
        'run_count': len(run_ids),
        'service_count': len(services),
    })
    return {'id': identity, 'deleted': True, 'deleted_jobs': len(job_ids), 'deleted_runs': len(run_ids)}
