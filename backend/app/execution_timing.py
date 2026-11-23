"""Read-only lifecycle timing; never infer process time from logs or result success."""
from datetime import datetime, timezone
import math
from sqlalchemy import select, func
from .core import Event
from .result_views import per_episode_process, TERMINAL

RECEIVED = '_platform_received_at'


def utc(value):
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    if not isinstance(value, datetime): return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def interval(start, end, at, *, running=False, source='platform_lifecycle', quality='recorded'):
    start, end, at = utc(start), utc(end), utc(at)
    stop = at if running else end
    seconds = (stop-start).total_seconds() if start is not None and stop is not None else None
    valid = seconds is not None and math.isfinite(seconds) and seconds >= 0
    return dict(state=('running' if running else 'finished') if valid else 'unknown',
                started_at=start, finished_at=None if running else end,
                seconds=seconds if valid else None, source=source, quality=quality if valid else 'unknown')


def pending_starts(session, run_ids):
    ids = list(dict.fromkeys(run_ids))
    result = {}
    # Bound IN parameters for SQLite and PostgreSQL alike; no per-run queries.
    for offset in range(0, len(ids), 500):
        rows = session.execute(select(Event.run_id, func.min(Event.created_at)).where(
            Event.run_id.in_(ids[offset:offset+500]), Event.new_status == 'PENDING').group_by(Event.run_id))
        result.update(rows.all())
    return result


def attempt(job, run, at, pending=None):
    terminal = run.status in TERMINAL
    start = run.enqueued_at
    execution = interval(start, run.finished_at, at, running=not terminal)
    if start is None and run.started_at is None and run.status in ('PENDING', 'RESERVED', 'ENQUEUED', 'CANCELLED') and run.exit_code is None:
        execution['state'] = 'not_started'
    fallback = pending is None and run.attempt == 1
    pending = job.created_at if fallback else pending
    waiting = interval(pending, start or (run.finished_at if terminal and execution['state'] == 'not_started' else None), at,
                       running=execution['state'] == 'not_started' and not terminal, quality='fallback' if fallback else 'recorded')
    template = (job.client_spec or {}).get('template', {})
    return dict(as_of=utc(at), mode='per_episode' if per_episode_process(template) else 'whole_task',
                execution=execution, waiting=waiting, running_started_at=utc(run.started_at))


def batch(batch, runs, at):
    runs = list(runs)
    complete = bool(runs) and all(r.status in TERMINAL for r in runs)
    end = max(utc(r.finished_at) for r in runs) if complete and all(utc(r.finished_at) for r in runs) else None
    elapsed = interval(batch.created_at, end, at, running=bool(runs) and not complete)
    return dict(as_of=utc(at), elapsed=elapsed)


def original(event):
    return {k:v for k,v in event.items() if k != RECEIVED} if event else event


def episode(template, row, run_status, at):
    if not per_episode_process(template): return None
    started = row.started if row else None
    result = row.result if row else None
    timing = (result or {}).get('timing')
    common = dict(as_of=utc(at), started_at=None, finished_at=None, seconds=None,
                  source='runner_monotonic', quality='unknown', state='unknown', clock_adjusted=False)
    if timing:
        return {**common, 'started_at':utc(timing['started_at']), 'finished_at':utc(timing['finished_at']),
                'seconds':timing['duration_seconds'], 'quality':'recorded', 'state':'finished',
                'clock_adjusted':timing['clock_adjusted']}
    legacy = (result or {}).get('runtime')
    if type(legacy) in (int, float) and math.isfinite(legacy) and legacy >= 0:
        return {**common, 'seconds':legacy, 'source':'legacy_reported', 'quality':'fallback', 'state':'finished'}
    if result: return common
    if not started: return {**common, 'state':'not_started'}
    if run_status in TERMINAL: return {**common, 'state':'interrupted'}
    received = started.get(RECEIVED)
    measured = interval(received, None, at, running=True, source='platform_received', quality='estimated')
    return {**common, **measured} if received else {**common, 'state':'running'}
