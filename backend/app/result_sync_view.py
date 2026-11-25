"""Read-only delivery status for the latest or selected execution attempts."""
from datetime import timedelta, timezone

from sqlalchemy import select

from .core import ExecutionRecovery, ExecutionRecoveryControl, RunLogState, now

DELAY_WARNING = timedelta(minutes=5)


def for_runs(session, runs, *, at=None):
    runs = list(runs)
    if not runs:
        return {}
    at = at or now()
    ids = [run.id for run in runs]
    recovery = {run_id: snapshot for run_id, snapshot in session.execute(
        select(ExecutionRecovery.run_id, ExecutionRecoveryControl.snapshot)
        .outerjoin(ExecutionRecoveryControl,
                   ExecutionRecoveryControl.execution_id == ExecutionRecovery.execution_id)
        .where(ExecutionRecovery.run_id.in_(ids)))}
    gaps = set(session.scalars(select(RunLogState.run_id).where(
        RunLogState.run_id.in_(ids), RunLogState.truncated.is_(True))))
    result = {}
    for run in runs:
        snapshot = recovery.get(run.id)
        state = snapshot.get('result_sync') if snapshot else None
        if run.id not in recovery:
            state = 'legacy'
        elif state not in ('pending', 'complete', 'failed'):
            state = 'in_progress' if run.status not in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'CANCELLED') else 'unknown'
        finished_at = run.finished_at
        if finished_at is not None and finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        delayed = (state == 'pending' and finished_at is not None
                   and at - finished_at > DELAY_WARNING)
        result[run.id] = {'state': state, 'delayed': delayed,
                          'log_truncated': run.id in gaps,
                          'error': snapshot.get('result_sync_error') if state == 'failed' else None}
    return result
