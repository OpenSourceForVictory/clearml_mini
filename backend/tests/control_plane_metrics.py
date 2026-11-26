"""Read-only sampling for a disposable load database, never platform credentials."""
import asyncio
import json
import subprocess
import time

from sqlalchemy import text


def sample(engine, container=None):
    began = time.monotonic()
    with engine.connect() as connection:
        activity = [dict(row._mapping) for row in connection.execute(text("""
            SELECT state, wait_event_type, count(*) AS connections,
                   max(extract(epoch FROM clock_timestamp()-query_start)) AS max_query_age_seconds
            FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
            GROUP BY state, wait_event_type
        """))]
        waiting = connection.scalar(text("""SELECT count(*) FROM pg_locks
            WHERE NOT granted AND pid IN (SELECT pid FROM pg_stat_activity WHERE datname=current_database())"""))
        database = dict(connection.execute(text("""SELECT xact_commit,xact_rollback,blks_read,blks_hit,deadlocks,
            temp_bytes FROM pg_stat_database WHERE datname=current_database()""")).one()._mapping)
        wal = int(connection.scalar(text('SELECT wal_bytes FROM pg_stat_wal')))
        expired_leases = int(connection.scalar(text("""SELECT count(*) FROM evaluation_runs
            WHERE lease_expires_at <= now() AND status IN ('STARTING','RUNNING','CANCEL_REQUESTED')""")))
    for row in activity:
        if row['max_query_age_seconds'] is not None:
            row['max_query_age_seconds'] = float(row['max_query_age_seconds'])
    result = {'activity':activity, 'waiting_locks':waiting, 'database':database,
              'cluster_wal_bytes':wal, 'expired_leases':expired_leases}
    if container:
        try:
            completed = subprocess.run(['docker','stats','--no-stream','--format','{{json .}}',container],
                                       capture_output=True, text=True, timeout=4, check=True)
            stats = json.loads(completed.stdout)
            result['postgres_container'] = {key:stats[key] for key in ('CPUPerc','MemUsage','BlockIO','PIDs')}
        except (subprocess.SubprocessError, ValueError, KeyError) as exc:
            result['postgres_stats_error'] = type(exc).__name__
    result['sample_seconds'] = time.monotonic()-began
    return result


def expired_leases_during_load(samples):
    """Keep lease checks inside the traffic window, before log drain begins."""
    return max((row['expired_leases'] for row in samples
                if row.get('phase') in ('load', 'boundary')), default=0)


async def observe(work, engine, *, container=None, interval=10, load_seconds=None):
    started = time.monotonic()
    task = asyncio.create_task(work)
    samples = []
    boundary_sampled = load_seconds is None
    try:
        while True:
            row = await asyncio.to_thread(sample, engine, container)
            row['elapsed_seconds'] = time.monotonic()-started
            if not boundary_sampled:
                row['phase'] = 'boundary' if row['elapsed_seconds'] >= load_seconds else 'load'
                if row['phase'] == 'boundary':
                    boundary_sampled = True
            else:
                row['phase'] = 'drain'
            samples.append(row)
            if task.done():
                break
            timeout = interval if boundary_sampled else min(interval, max(0, started+load_seconds-time.monotonic()))
            await asyncio.wait([task], timeout=timeout)
        result = await task
        result['database_samples'] = samples
        return result
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def with_outage(work, stop, restart, *, after, duration):
    """Keep load running during a deliberate role outage; always restore it."""
    started = time.monotonic()
    evidence = {}
    async def fault():
        await asyncio.sleep(after)
        stopping = asyncio.create_task(asyncio.to_thread(stop))
        try:
            await asyncio.shield(stopping)
            evidence['stopped_at_seconds'] = time.monotonic()-started
            await asyncio.sleep(duration)
        finally:
            try:
                await stopping
            finally:
                await asyncio.to_thread(restart)
                evidence['restarted_at_seconds'] = time.monotonic()-started
    task, interruption = asyncio.create_task(work), asyncio.create_task(fault())
    try:
        result, _ = await asyncio.gather(task, interruption)
        result['during_load_outage'] = evidence
        return result
    finally:
        for pending in (task, interruption):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, interruption, return_exceptions=True)
