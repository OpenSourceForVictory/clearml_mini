"""Transactional admission and reconciliation; PostgreSQL is the only queue."""
import logging
import os
import threading
from datetime import timedelta, timezone
from sqlalchemy import or_, select, text
from .core import EvaluationBatch, Event, Job, Pool, Run, SchedulerState, Slot, User, Worker, now, uid

ACTIVE = ('RESERVED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED')
TERMINAL = ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT')
LOCK_ID = 82510401


def seconds(name, default):
    return max(1, int(os.getenv(name, str(default))))


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def online(slot, at=None):
    return bool(slot.enabled and slot.last_seen_at and
                (at or now()) - utc(slot.last_seen_at) < timedelta(seconds=seconds('WORKER_TTL_SECONDS', 30)))


def lock(s, wait=True):
    if s.bind.dialect.name != 'postgresql':
        return True  # SQLite is only used by serial unit tests.
    if wait:
        s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK_ID})
        return True
    return s.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_ID})


def transition(s, run, status, message=None):
    job = s.get(Job, run.job_id)
    old = run.status
    run.status = job.status = status
    if status in TERMINAL:
        run.finished_at = job.finished_at = now()
        run.error = job.error = message
    s.add(Event(job_id=job.id, run_id=run.id, old_status=old, new_status=status, message=message))


def execution_timeout(job):
    limit = seconds('EXECUTION_TIMEOUT_SECONDS', 3600)
    return min(limit, job.client_spec['timeout_seconds']) if job.client_spec else limit


def reconcile(s, at):
    for run in s.scalars(select(Run).where(Run.status.in_(ACTIVE))).all():
        if run.status == 'RESERVED':
            if utc(run.reservation_expires_at) <= at:
                # No worker has claimed it, so no container can have been started.
                transition(s, run, 'PENDING', 'unclaimed reservation expired')
                run.slot_id = run.execution_id = run.reservation_expires_at = None
            continue
        expired = run.lease_expires_at and utc(run.lease_expires_at) + timedelta(seconds=seconds('LOST_WORKER_GRACE_SECONDS', 30)) <= at
        overrun = run.started_at and utc(run.started_at) + timedelta(seconds=execution_timeout(s.get(Job, run.job_id))) <= at
        startup = run.status == 'STARTING' and utc(run.enqueued_at) + timedelta(seconds=seconds('STARTUP_TIMEOUT_SECONDS', 120)) <= at
        if expired or overrun or startup:
            transition(s, run, 'TIMED_OUT', 'worker lease or execution deadline expired')
            run.cleanup_confirmed = False
            s.get(Slot, run.slot_id).quarantined = True


def telemetry_fresh(slot, at):
    return (slot.telemetry_at is not None and slot.free_vram_mb is not None
            and slot.gpu_utilization is not None
            and timedelta(0) <= at - utc(slot.telemetry_at) < timedelta(seconds=15))


def endpoint_conflict(job, held_jobs):
    from runner.cs_contract import endpoint_parts
    if not job.client_spec:
        return False
    def key(spec):
        parts, port = endpoint_parts(spec['endpoint'])
        return parts.hostname.lower().rstrip('.'), port
    wanted = key(job.client_spec)
    exclusive = job.client_spec['template'].get('endpoint_exclusive', False)
    return any(other.client_spec and key(other.client_spec) == wanted and
               (exclusive or other.client_spec['template'].get('endpoint_exclusive', False)) for other in held_jobs)


def eligible(slot, pool, job, held, at):
    """Both physical capacity and observed headroom must fit; utilization is advisory."""
    if slot.disk_min_free_mb is not None and (slot.disk_free_mb is None or slot.disk_free_mb < slot.disk_min_free_mb
            or slot.disk_at is None or (at-utc(slot.disk_at)).total_seconds() >= 15):
        return False
    if job.client_spec and (slot.protocol_version < 3 or
                            job.client_spec['template']['image'] not in (slot.client_images or [])):
        return False
    if job.required_gpu_model and slot.gpu_model != job.required_gpu_model:
        return False
    requested = job.requested_vram_gb * 1024
    if job.gpu_mode == 'exclusive':
        if held:
            return False
        if not requested:
            return True  # Backward-compatible exclusive protocol.
    else:
        if not pool.allow_shared or slot.protocol_version < 2 or requested <= 0:
            return False
        if len(held) >= pool.max_tasks_per_gpu or any(r.exclusive for r in held):
            return False
    if slot.protocol_version < 2 or not telemetry_fresh(slot, at):
        return False
    margin = pool.vram_reserve_gb * 1024
    if requested + sum(r.reserved_vram_mb for r in held) > slot.vram_mb - margin:
        return False
    # RUNNING telemetry already includes actual usage. Unstarted reservations do not.
    starting = sum(r.reserved_vram_mb for r in held if r.started_at is None)
    if requested + starting > slot.free_vram_mb - margin:
        return False
    return job.gpu_mode == 'exclusive' or slot.gpu_utilization < pool.max_gpu_utilization


def tick(factory, at=None):
    from .storage import ready
    from . import memberships
    at = at or now()
    with factory() as s:
        if not lock(s, wait=False):
            return 0
        reconcile(s, at)
        memberships.reconcile(s)
        s.flush()
        # A timed-out worker may still be running its container. Keep its global,
        # user and node resource credits until cleanup is explicitly confirmed.
        active = s.scalars(select(Run).where(or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False)))).all()
        pools = {p.id: p for p in s.scalars(select(Pool).where(Pool.enabled))}
        workers = {w.id: w for w in s.scalars(select(Worker).where(Worker.enabled))}
        slots = {x.id: x for x in s.scalars(select(Slot))}
        by_slot = {x.id: [] for x in slots.values()}
        for r in active:
            if r.slot_id in by_slot:
                by_slot[r.slot_id].append(r)
        held_jobs = [s.get(Job, r.job_id) for r in active]
        batch_counts = {}
        batches = {b.id: b for b in s.scalars(select(EvaluationBatch))}
        counts, node_counts, node_cpu, node_ram = {}, {}, {}, {}
        for r in active:
            job = s.get(Job, r.job_id)
            batch_counts[job.batch_id] = batch_counts.get(job.batch_id, 0) + 1
            counts[job.owner_id] = counts.get(job.owner_id, 0) + 1
            slot = slots.get(r.slot_id)
            pool = s.get(Pool, job.pool_id)
            if slot:
                n = slot.worker_id
                node_counts[n] = node_counts.get(n, 0) + 1
                node_cpu[n] = node_cpu.get(n, 0) + r.reserved_cpu
                node_ram[n] = node_ram.get(n, 0) + r.reserved_ram_gb
        free = [x for x in slots.values() if not x.quarantined
                and x.worker_id in workers and x.pool_id in pools and online(x, at)
                and memberships.ready(x) and ready(workers[x.worker_id], x, at)]
        pending = s.scalars(select(Job).join(User, User.id == Job.owner_id).where(
            Job.status == 'PENDING', User.active).order_by(Job.created_at, Job.id)).all()
        cursor = s.get(SchedulerState, 'rotation')
        if not cursor:
            cursor = SchedulerState(key='rotation', value={})
            s.add(cursor)
        owners = sorted({j.owner_id for j in pending})
        last = cursor.value.get('owner', '')
        owners = [u for u in owners if u > last] + [u for u in owners if u <= last]
        admitted = 0
        while free and owners and len(active) + admitted < seconds('MAX_ACTIVE_GLOBAL', 2):
            made_progress = False
            for owner in owners:
                if len(active) + admitted >= seconds('MAX_ACTIVE_GLOBAL', 2):
                    break
                if counts.get(owner, 0) >= seconds('MAX_ACTIVE_PER_USER', 2):
                    continue
                candidates = [j for j in pending if j.owner_id == owner]
                rotation = s.get(SchedulerState, 'batch:'+owner)
                if not rotation:
                    rotation = SchedulerState(key='batch:'+owner, value={})
                    s.add(rotation)
                groups = sorted({j.batch_id or 'standalone' for j in candidates})
                previous = rotation.value.get('group', '')
                groups = [g for g in groups if g > previous] + [g for g in groups if g <= previous]
                order = {g: i for i, g in enumerate(groups)}
                candidates.sort(key=lambda j: order[j.batch_id or 'standalone'])
                for job in candidates:
                    if endpoint_conflict(job, held_jobs):
                        continue
                    if job.batch_id and batch_counts.get(job.batch_id, 0) >= batches[job.batch_id].max_concurrent:
                        continue
                    pool = pools.get(job.pool_id)
                    if not pool:
                        continue
                    free.sort(key=lambda x: (x.gpu_utilization if telemetry_fresh(x, at) else 101,
                                             len(by_slot[x.id]), x.node_name, x.gpu_index))
                    slot = next((x for x in free if x.pool_id == job.pool_id
                        and eligible(x, pool, job, by_slot[x.id], at)
                        and node_counts.get(x.worker_id, 0) < workers[x.worker_id].max_concurrent
                        and node_cpu.get(x.worker_id, 0) + pool.cpu_per_job <= workers[x.worker_id].cpu_capacity
                        and node_ram.get(x.worker_id, 0) + pool.ram_gb_per_job <= workers[x.worker_id].ram_gb_capacity), None)
                    if not slot:
                        continue
                    run = s.scalar(select(Run).where(Run.job_id == job.id).order_by(Run.attempt.desc()))
                    run.slot_id, run.queue_name, run.execution_id = slot.id, slot.queue_name, uid()
                    used = {r.allocation_index for r in by_slot[slot.id]}
                    run.allocation_index = next(i for i in range(len(used)+1) if i not in used)
                    run.exclusive = job.gpu_mode == 'exclusive'
                    run.reserved_vram_mb = slot.vram_mb if run.exclusive else job.requested_vram_gb * 1024
                    run.reserved_cpu, run.reserved_ram_gb = pool.cpu_per_job, pool.ram_gb_per_job
                    run.reserved_at = at
                    run.reservation_expires_at = at + timedelta(seconds=seconds('RESERVATION_SECONDS', 30))
                    job.queued_at = at
                    transition(s, run, 'RESERVED')
                    counts[owner] = counts.get(owner, 0) + 1
                    n = slot.worker_id
                    node_counts[n] = node_counts.get(n, 0) + 1
                    node_cpu[n] = node_cpu.get(n, 0) + pool.cpu_per_job
                    node_ram[n] = node_ram.get(n, 0) + pool.ram_gb_per_job
                    cursor.value = {'owner': owner}
                    rotation.value = {'group': job.batch_id or 'standalone'}
                    batch_counts[job.batch_id] = batch_counts.get(job.batch_id, 0) + 1
                    held_jobs.append(job)
                    by_slot[slot.id].append(run)
                    pending.remove(job)
                    admitted += 1
                    made_progress = True
                    break
            if not made_progress:
                break
        state = s.get(SchedulerState, 'heartbeat')
        if not state:
            state = SchedulerState(key='heartbeat', value={})
            s.add(state)
        state.value = {'at': at.isoformat()}
        state.updated_at = at
        s.commit()
        return admitted


def loop(factory, stop: threading.Event):
    while not stop.is_set():
        try:
            tick(factory)
        except Exception:
            logging.getLogger(__name__).exception('scheduler tick failed')
        stop.wait(seconds('SCHEDULER_TICK_SECONDS', 2))
