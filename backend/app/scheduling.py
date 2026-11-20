"""Transactional admission and reconciliation; PostgreSQL is the only queue."""
import logging
import os
import threading
from datetime import timedelta, timezone
from sqlalchemy import or_, select, text
from fastapi import HTTPException
from . import endpoints
from .core import EvaluationBatch, Event, Job, Pool, Run, SchedulerState, Slot, User, Worker, now, uid
from .freshness import RESOURCE_SAMPLE_SECONDS, recent

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


def fast_lock(s):
    """High-frequency reports must release their connection instead of queuing on admission."""
    if not lock(s, wait=False):
        raise HTTPException(503, 'control plane busy; retry shortly', headers={'Retry-After':'1'})


def transition(s, run, status, message=None):
    job = s.get(Job, run.job_id)
    old = run.status
    if old == 'RESERVED' and status in ('PENDING', 'CANCELLED'):
        run.assigned_endpoint = run.assigned_endpoint_mode = None
        run.assigned_service_instance_id=run.assigned_service_execution_id=run.assigned_service_key=None
        run.waiting_service=False
    run.status = job.status = status
    if status in TERMINAL:
        run.finished_at = job.finished_at = now()
        run.error = job.error = message
    s.add(Event(job_id=job.id, run_id=run.id, old_status=old, new_status=status, message=message))


def execution_timeout(job):
    if getattr(job, 'benchmark', None) == 'inference-service': return 31536000
    limit = seconds('EXECUTION_TIMEOUT_SECONDS', 3600)
    return job.client_spec['timeout_seconds'] if job.client_spec else limit


def reconcile(s, at):
    for run in s.scalars(select(Run).where(Run.status.in_(ACTIVE))).all():
        if run.status == 'RESERVED':
            if utc(run.reservation_expires_at) <= at:
                # No worker has claimed it, so no container can have been started.
                transition(s, run, 'PENDING', 'unclaimed reservation expired')
                run.slot_id = run.execution_id = run.reservation_expires_at = None
            continue
        expired = run.lease_expires_at and utc(run.lease_expires_at) + timedelta(seconds=seconds('LOST_WORKER_GRACE_SECONDS', 30)) <= at
        overrun = s.get(Job, run.job_id).benchmark != 'inference-service' and execution_timeout(s.get(Job, run.job_id)) > 0 and run.started_at and utc(run.started_at) + timedelta(seconds=execution_timeout(s.get(Job, run.job_id))) <= at
        startup = run.status == 'STARTING' and utc(run.enqueued_at) + timedelta(seconds=seconds('STARTUP_TIMEOUT_SECONDS', 120)) <= at
        if expired or overrun or startup:
            if overrun:
                message = f'客户端执行总时限已到（{execution_timeout(s.get(Job, run.job_id))} 秒，全部回合共用）'
            elif startup:
                message = f'执行启动超时（{seconds("STARTUP_TIMEOUT_SECONDS", 120)} 秒）'
            else:
                message = f'执行心跳租约过期（额外宽限 {seconds("LOST_WORKER_GRACE_SECONDS", 30)} 秒）'
            transition(s, run, 'TIMED_OUT', message)
            run.cleanup_confirmed = False
            s.get(Slot, run.slot_id).quarantined = True


def telemetry_fresh(slot, at):
    return (slot.free_vram_mb is not None and slot.gpu_utilization is not None
            and recent(slot.telemetry_at, at, RESOURCE_SAMPLE_SECONDS))



def runtime_mismatch(slot, job):
    """Explain the exact client/server execution requirement that this slot lacks."""
    template=(job.client_spec or {}).get('template',{})
    side='服务端' if getattr(job,'benchmark',None)=='inference-service' else '客户端'
    mode=template.get('execution_mode','docker')
    label=(f'{side}宿主机环境「{(template.get("host_runtime") or {}).get("environment_name","未指定") }」'
           if mode=='host' else f'{side}容器镜像「{template.get("image","未指定") }」')
    resources=(job.client_spec or {}).get('resources',{})
    if slot.protocol_version<16 and ((job.client_spec or {}).get('timeout_seconds')==0 or resources.get('cpu_unlimited') or resources.get('memory_unlimited')):
        return f'{label}的不限执行时长或CPU/内存上限需要 Worker 协议 16（当前 {slot.protocol_version}）'
    settings=template.get('host_runtime') or template.get('runtime') or {}
    if mode=='host' and settings.get('episode_error_policy')=='continue' and slot.protocol_version<17:
        return f'{label}的回合出错后继续需要 Worker 协议 17（当前 {slot.protocol_version}）'
    if mode=='host' and slot.protocol_version<15 and (settings.get('environment_source')=='login' or settings.get('episode_launch')=='per_episode'):
        return f'{label}的登录环境或逐回合启动需要 Worker 协议 15（当前 {slot.protocol_version}）'
    if slot.protocol_version<14 and ('command_mode' in settings or (template.get('execution_mode')!='host' and ('working_directory' in settings or settings.get('interpreter','python3') not in ('python','python3')))):
        return f'{label}需要 Worker 协议 14（当前 {slot.protocol_version}）'
    if template.get('execution_mode')=='host' and (template.get('host_runtime') or {}).get('working_directory') is not None and slot.protocol_version<13:
        return f'{label}的业务执行目录需要 Worker 协议 13（当前 {slot.protocol_version}）'
    if template.get('resource_requirements') is not None and template.get('execution_mode')=='host' and slot.protocol_version<11:
        return f'{label}的资源申请需要 Worker 协议 11（当前 {slot.protocol_version}）'
    if (job.client_spec or {}).get('template', {}).get('parameter_schema') is not None and slot.protocol_version < 8:
        return f'{label}的业务参数需要 Worker 协议 8（当前 {slot.protocol_version}）'
    if mode not in (slot.execution_modes or ['docker']):
        return f'{label}未就绪：该设备未启用{"宿主机" if mode=="host" else "容器"}执行'
    if job.client_spec and (job.client_spec['template'].get('execution_mode') == 'host' or job.client_spec['template'].get('integration_mode') == 'sdk-command') and slot.protocol_version < 7:
        return f'{label}需要 Worker 协议 7（当前 {slot.protocol_version}）'
    return None


def eligibility_codes(slot, pool, job, held, at):
    """Ordered admission failures shared by scheduler and read-only preflight."""
    if slot.disk_min_free_mb is not None and (slot.disk_free_mb is None or slot.disk_free_mb < slot.disk_min_free_mb
            or not recent(slot.disk_at, at, RESOURCE_SAMPLE_SECONDS)):
        return ['disk_capacity']
    if runtime_mismatch(slot, job):return ['client_runtime']
    template=(job.client_spec or {}).get('template',{})
    mode=template.get('execution_mode','docker')
    if job.client_spec and job.client_spec['template'].get('execution_mode', 'docker') == 'docker' and (slot.protocol_version < 3 or
                            job.client_spec['template']['image'] not in (slot.client_images or [])):
        return ['client_image']
    requested_type=((job.client_spec or {}).get('resources') or {}).get('accelerator_type','gpu')
    actual_type=getattr(slot,'accelerator_type',None) or 'gpu'
    if requested_type!=actual_type:return ['accelerator_type']
    if requested_type=='npu':
        capability=f'npu:{mode}:{job.gpu_mode}'
        if slot.protocol_version<11 or capability not in (getattr(slot,'accelerator_backends',None) or []):return ['accelerator_backend']
        if not telemetry_fresh(slot,at):return ['telemetry_stale']
    if requested_type=='gpu' and job.gpu_mode=='shared' and getattr(slot,'accelerator_backends',None):
        if f'gpu:{mode}:shared' not in slot.accelerator_backends:return ['accelerator_backend']
    if job.required_gpu_model and slot.gpu_model != job.required_gpu_model:
        return ['gpu_model']
    requested = job.requested_vram_gb * 1024
    if job.gpu_mode == 'exclusive':
        if held:
            return ['gpu_busy']
        if not requested:
            return []  # Backward-compatible exclusive protocol.
    else:
        if not pool.allow_shared or slot.protocol_version < 2 or requested <= 0:
            return ['shared_unsupported']
        if len(held) >= pool.max_tasks_per_gpu or any(r.exclusive for r in held):
            return ['gpu_busy']
    if slot.protocol_version < 2 or not telemetry_fresh(slot, at):
        return ['telemetry_stale']
    margin = pool.vram_reserve_gb * 1024
    if requested > slot.vram_mb - margin:
        return ['vram_capacity']
    committed = requested + sum(r.reserved_vram_mb for r in held)
    if committed > slot.vram_mb - margin:
        return ['vram_busy']
    # Without per-task usage attribution, free memory can also belong to external
    # processes. Charge all platform reservations against observed free memory.
    if committed > slot.free_vram_mb - margin:
        return ['vram_busy']
    return [] if job.gpu_mode == 'exclusive' or slot.gpu_utilization < pool.max_gpu_utilization else ['gpu_utilization']


def eligible(slot, pool, job, held, at):
    return not eligibility_codes(slot, pool, job, held, at)


def slot_codes(worker, slot, at):
    from . import storage, memberships, image_runtime
    result = []
    if getattr(worker,'upgrade_state',None) in ('DRAINING','APPLYING','VERIFYING','ROLLING_BACK','FAILED'):result.append('worker_upgrade')
    from .node_agents import admission
    if not admission(worker): result.append('node_agent_not_ready')
    if not worker.enabled or not online(slot, at): result.append('offline')
    if slot.quarantined: result.append('quarantined')
    if not memberships.ready(slot): result.append('membership_pending')
    if not storage.ready(worker, slot, at): result.append('storage_pending')
    if not image_runtime.ready(worker, slot, at): result.append('runtime_pending')
    return result


def workload_codes(worker, slot, job):
    role = 'server' if getattr(job, 'benchmark', None) == 'inference-service' else 'client'
    if role not in (worker.roles if worker.roles is not None else ['client']): return ['node_role_mismatch']
    if getattr(job, 'target_slot_id', None) and job.target_slot_id != slot.id: return ['target_slot_mismatch']
    if role == 'server' and slot.protocol_version < 7: return ['client_runtime']
    return []


def node_codes(worker, pool, count, cpu, ram, pending_ram=0):
    from .node_resources import host_codes
    result = host_codes(worker, pool, cpu, ram, pending_ram=pending_ram)
    if count >= worker.max_concurrent: result.append('node_concurrency')
    if pool.cpu_per_job and cpu + pool.cpu_per_job > worker.cpu_capacity and 'node_cpu' not in result: result.append('node_cpu')
    if pool.ram_gb_per_job and ram + pool.ram_gb_per_job > worker.ram_gb_capacity and 'node_ram' not in result: result.append('node_ram')
    return result


def tick(factory, at=None):
    from .storage import ready
    from . import memberships, image_runtime
    live_clock = at is None
    at = at or now()
    with factory() as s:
        if not lock(s, wait=False):
            return 0
        reconcile(s, at)
        from . import orchestration_scheduler
        orchestration_scheduler.reconcile(s,at)
        memberships.reconcile(s)
        s.flush()
        # A timed-out worker may still be running its container. Keep its global,
        # user and node resource credits until cleanup is explicitly confirmed.
        active = s.scalars(select(Run).where(or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False)))).all()
        pools = {p.id: p for p in s.scalars(select(Pool).where(Pool.enabled))}
        workers = {w.id: w for w in s.scalars(select(Worker).where(Worker.enabled))}
        slots = {x.id: x for x in s.scalars(select(Slot).where(Slot.deleted_at.is_(None)))}
        by_slot = {x.id: [] for x in slots.values()}
        for r in active:
            if r.slot_id in by_slot:
                by_slot[r.slot_id].append(r)
        endpoint_held = endpoints.holdings(active)
        batch_counts = {}
        batches = {b.id: b for b in s.scalars(select(EvaluationBatch))}
        counts, node_counts, node_cpu, node_ram, pending_ram = {}, {}, {}, {}, {}
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
                if r.started_at is None:
                    pending_ram[n] = pending_ram.get(n, 0) + r.reserved_ram_gb
        # Reports may commit after tick entry while reconciliation/queries run.
        # Compare the loaded snapshot to read completion, not tick entry time.
        if live_clock:
            at = now()
        free = [x for x in slots.values() if x.worker_id in workers and x.pool_id in pools
                and not slot_codes(workers[x.worker_id], x, at)]
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
                # Batch priority reorders only this owner's queue. Owner-level
                # round robin above remains the cross-user fairness boundary.
                candidates.sort(key=lambda j: (
                    -(batches[j.batch_id].priority if j.batch_id in batches else 0),
                    utc(batches[j.batch_id].created_at) if j.batch_id in batches else utc(j.created_at),
                    utc(j.created_at), j.id))
                for job in candidates:
                    if job.batch_id and batches[job.batch_id].scheduling_paused:
                        continue
                    if job.batch_id and batch_counts.get(job.batch_id, 0) >= batches[job.batch_id].max_concurrent:
                        continue
                    selected_endpoint = endpoints.choose(job.client_spec, endpoint_held,s)
                    if (job.client_spec or {}).get('service_mode')=='managed' and selected_endpoint is None:
                        records=orchestration_scheduler.allocate_pair(s,job,at)
                        if not records:continue
                    else:
                        if job.client_spec and job.client_spec.get('service_mode')!='none' and job.benchmark!='inference-service' and selected_endpoint is None:
                            continue
                        pool=pools.get(job.pool_id)
                        if not pool:continue
                        from .job_resources import budget
                        request=budget(pool,job)
                        free.sort(key=lambda x:(x.gpu_utilization if telemetry_fresh(x,at) else 101,len(by_slot[x.id]),x.node_name,x.gpu_index))
                        slot=next((x for x in free if x.pool_id==job.pool_id
                            and not workload_codes(workers[x.worker_id],x,job)
                            and eligible(x,pool,job,by_slot[x.id],at)
                            and not image_runtime.blockers(workers[x.worker_id],x,job.client_spec,at)
                            and not node_codes(workers[x.worker_id],request,node_counts.get(x.worker_id,0),
                                               node_cpu.get(x.worker_id,0),node_ram.get(x.worker_id,0),pending_ram.get(x.worker_id,0))),None)
                        if not slot:continue
                        run=orchestration_scheduler.reserve(s,job,slot,at,selected_endpoint)
                        records=[(job,slot,run,selected_endpoint)]
                    for allocated,slot,run,selection in records:
                        counts[owner]=counts.get(owner,0)+1
                        n=slot.worker_id
                        node_counts[n]=node_counts.get(n,0)+1
                        node_cpu[n]=node_cpu.get(n,0)+run.reserved_cpu
                        node_ram[n]=node_ram.get(n,0)+run.reserved_ram_gb
                        pending_ram[n]=pending_ram.get(n,0)+run.reserved_ram_gb
                        batch_counts[allocated.batch_id]=batch_counts.get(allocated.batch_id,0)+1
                        if selection:endpoint_held.append(selection)
                        by_slot[slot.id].append(run)
                        if allocated in pending:pending.remove(allocated)
                        admitted+=1
                    cursor.value={'owner':owner}
                    made_progress = True
                    break
            if not made_progress:
                break
        admitted += orchestration_scheduler.fill_desired_instances(s,at)
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
