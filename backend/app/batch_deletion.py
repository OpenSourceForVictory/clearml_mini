"""Batch deletion with a durable tombstone and removal of batch-owned work."""
from fastapi import HTTPException
from sqlalchemy import select
from .core import (
    DataCleanupItem,
    EpisodeRecord,
    EvaluationBatch,
    EvaluationOrchestration,
    Event,
    ExecutionRecovery, ExecutionRecoveryClaim, ExecutionRecoveryControl, ExecutionRecoveryReceipt,
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


def _members(s, identity):
    services = list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id == identity)))
    service_ids = [item.id for item in services]
    jobs = list(s.scalars(select(Job).where((Job.batch_id == identity) | Job.service_instance_id.in_(service_ids))))
    return services, jobs


def _safe_to_purge(s, services, jobs):
    if any(item.desired_running for item in services) or any(j.status not in TERMINAL for j in jobs):
        return False
    run_ids = list(s.scalars(select(Run.id).where(Run.job_id.in_([j.id for j in jobs]))))
    if s.scalar(select(Run.id).where(Run.id.in_(run_ids), Run.cleanup_confirmed.is_(False)).limit(1)):
        return False
    for execution in s.scalars(select(ExecutionRecovery).where(ExecutionRecovery.run_id.in_(run_ids))):
        control = s.get(ExecutionRecoveryControl, execution.execution_id)
        if (not control or control.snapshot.get('identity_conflict')
                or not control.snapshot.get('identity')
                or control.snapshot.get('cleanup_confirmed') is not True
                or control.snapshot.get('result_sync') not in ('complete', 'failed')
                or control.snapshot.get('platform_status') not in TERMINAL):
            return False
    return True


def purge_if_safe(s, batch):
    """Called under the scheduler lock; keep tombstone and execution authority."""
    services, jobs = _members(s, batch.id)
    if not batch.deleted_at or not _safe_to_purge(s, services, jobs):
        return None

    # Keep the batch row as a tombstone so idempotency keys and deleted-state
    # guards remain effective, but remove all task-owned execution data.  The
    # explicit order is required on PostgreSQL because these tables do not use
    # database-level ON DELETE CASCADE.
    job_ids = [item.id for item in jobs]
    run_ids = list(s.scalars(select(Run.id).where(Run.job_id.in_(job_ids)).order_by(Run.id).with_for_update())) if job_ids else []
    recovery_ids = list(s.scalars(select(ExecutionRecovery.execution_id).where(
        ExecutionRecovery.run_id.in_(run_ids)))) if run_ids else []
    # Once final sync is sealed, only a minimal same-identity ACK record is
    # needed. Remove raw receipts and immutable task snapshots with the batch.
    for execution_id in recovery_ids:
        control = s.get(ExecutionRecoveryControl, execution_id)
        snapshot = control.snapshot
        control.snapshot = {key: snapshot[key] for key in (
            'identity', 'cleanup_confirmed', 'result_sync', 'platform_status')}
    for model, column in (
        (ExecutionRecoveryReceipt, ExecutionRecoveryReceipt.execution_id),
        (ExecutionRecoveryClaim, ExecutionRecoveryClaim.execution_id),
    ):
        for row in s.scalars(select(model).where(column.in_(recovery_ids))):
            s.delete(row)
        s.flush()
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
    orchestration = s.get(EvaluationOrchestration, batch.id)
    if orchestration:
        s.delete(orchestration)
    return {'id': batch.id, 'deleted': True, 'pending_cleanup': False,
            'deleted_jobs': len(job_ids), 'deleted_runs': len(run_ids)}


def remove(s, identity, user):
    # Same transaction lock as claims, retries and service lifecycle changes.
    lock(s)
    batch = s.get(EvaluationBatch, identity)
    if not batch or (batch.owner_id != user.id and user.role != 'admin'):
        raise HTTPException(404, 'batch not found')
    if batch.deleted_at:
        return purge_if_safe(s, batch) or {'id': identity, 'deleted': True, 'pending_cleanup': True}
    services, jobs = _members(s, identity)
    batch.deleted_at = now()
    batch.scheduling_paused = True
    batch.control_revision += 1
    batch.control_updated_at = batch.deleted_at
    for item in services:
        if item.desired_running:
            item.desired_running = False
            item.revision += 1
    from .batch_controls import cancel_job
    for job in jobs:
        if job.status in TERMINAL:
            continue
        before = job.status
        cancel_job(s, job)
        if before in ('PENDING', 'RESERVED'):
            # No claim can exist before STARTING was committed. The original
            # reservation has no process that could require cleanup.
            job.runs[-1].cleanup_confirmed = True
    from .administration import audit
    audit(s, user, 'batch.delete', identity, {
        'job_count': len(jobs),
        'run_count': sum(len(job.runs) for job in jobs),
        'service_count': len(services),
        'pending_cleanup': not _safe_to_purge(s, services, jobs),
    })
    return purge_if_safe(s, batch) or {'id': identity, 'deleted': True, 'pending_cleanup': True}
