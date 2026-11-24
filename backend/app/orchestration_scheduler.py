"""Managed service dependencies using the existing Run reservation ledger.

A waiting client's RESERVED Run is its startup budget, never a second resource
counter. It cannot be claimed before the bound service generation is healthy.
"""
from types import SimpleNamespace
from sqlalchemy import select,or_
from .core import EvaluationOrchestration,EvaluationBatch,ServiceInstance,Worker,Slot,Pool,Job,Run,User,SchedulerState,now,uid
from . import scheduling as admission,services,image_runtime,service_dependencies
from .job_resources import budget
from .service_network import choose_port


def server_job(definition,owner_id):
    resource=definition['resources']
    return SimpleNamespace(owner_id=owner_id,pool_id=definition['pool_id'],benchmark='inference-service',target_slot_id=definition.get('slot_id'),
        client_spec={'resources':resource,'workload':'service','template':definition['template'],'task_input_version':1},gpu_mode=resource['gpu_mode'],
        requested_vram_gb=resource['requested_vram_gb'],required_gpu_model=resource.get('required_gpu_model'),
        requested_cpu=resource.get('cpus'),requested_ram_gb=resource.get('ram_gb'))


class Planner:
    def __init__(self,s,at):
        self.s=s;self.at=at
        self.workers={x.id:x for x in s.scalars(select(Worker).where(Worker.deleted_at.is_(None)))}
        self.slots=list(s.scalars(select(Slot).where(Slot.deleted_at.is_(None)).order_by(Slot.node_name,Slot.gpu_index)))
        self.pools={x.id:x for x in s.scalars(select(Pool).where(Pool.enabled))}
        self.active=list(s.scalars(select(Run).where(or_(Run.status.in_(admission.ACTIVE),Run.cleanup_confirmed.is_(False)))))
        self.jobs={r.job_id:s.get(Job,r.job_id) for r in self.active}

    def fits(self,job,slot,extra=()):
        pool=self.pools.get(job.pool_id);worker=self.workers.get(slot.worker_id)
        if not pool or slot.pool_id!=pool.id or not worker:return False
        runs=self.active+list(extra)
        held=[r for r in runs if r.slot_id==slot.id]
        node_slots={x.id for x in self.slots if x.worker_id==worker.id}
        node=[r for r in runs if r.slot_id in node_slots]
        return not (admission.slot_codes(worker,slot,self.at) or admission.workload_codes(worker,slot,job) or
                    admission.eligibility_codes(slot,pool,job,held,self.at) or image_runtime.blockers(worker,slot,job.client_spec,self.at) or
                    admission.node_codes(worker,budget(pool,job),len(node),sum(r.reserved_cpu for r in node),sum(r.reserved_ram_gb for r in node),
                                         sum(r.reserved_ram_gb for r in node if not r.started_at)))

    def structural_pair(self,client,definition):
        """Keep the tri-state admission contract used by preflight."""
        return self.structural_pair_diagnosis(client,definition)[0]

    def structural_pair_diagnosis(self,client,definition):
        """Explain only configured capacity contradictions, not transient occupancy."""
        server=server_job(definition,client.owner_id)
        def targets(job):
            role='server' if job.benchmark=='inference-service' else 'client'
            return [x for x in self.slots if x.pool_id==job.pool_id and x.worker_id in self.workers
                    and role in (self.workers[x.worker_id].roles or ['client'])
                    and (not getattr(job,'target_slot_id',None) or job.target_slot_id==x.id)]
        servers,clients=targets(server),targets(client)
        if not servers or not clients:return None,[]
        def errors(job,slot,side):
            p=self.pools[job.pool_id];w=self.workers[slot.worker_id];r=budget(p,job)
            result=[]
            if job.required_gpu_model and slot.gpu_model!=job.required_gpu_model:result.append(side+'设备型号不匹配')
            if r.cpu_per_job and r.cpu_per_job>w.cpu_capacity:result.append(side+'单项 CPU 超过节点容量')
            if r.ram_gb_per_job and r.ram_gb_per_job>w.ram_gb_capacity:result.append(side+'单项内存超过节点容量')
            if job.requested_vram_gb and job.requested_vram_gb*1024>slot.vram_mb-p.vram_reserve_gb*1024:
                result.append(side+'单项设备内存超过卡容量')
            return result
        usable_servers=[x for x in servers if not errors(server,x,'服务端')]
        usable_clients=[x for x in clients if not errors(client,x,'客户端')]
        if not usable_servers or not usable_clients:
            reasons=set()
            if not usable_servers:
                for slot in servers:reasons.update(errors(server,slot,'服务端'))
            if not usable_clients:
                for slot in clients:reasons.update(errors(client,slot,'客户端'))
            return False,sorted(reasons)
        reasons=set()
        for ss in usable_servers:
            for cs in usable_clients:
                pair_reasons=[]
                if ss.id==cs.id:
                    p=self.pools[server.pool_id]
                    if server.gpu_mode!='shared' or client.gpu_mode!='shared':
                        pair_reasons.append('同卡不能同时容纳独占请求')
                    elif p.max_tasks_per_gpu<2:
                        pair_reasons.append('同卡共享任务上限小于 2')
                    elif (server.requested_vram_gb+client.requested_vram_gb+p.vram_reserve_gb)*1024>ss.vram_mb:
                        pair_reasons.append('同卡双方设备内存申请之和超过容量')
                if ss.worker_id==cs.worker_id:
                    w=self.workers[ss.worker_id];sr=budget(self.pools[server.pool_id],server);cr=budget(self.pools[client.pool_id],client)
                    if w.max_concurrent<2:pair_reasons.append('同机并发上限小于 2')
                    if sr.cpu_per_job+cr.cpu_per_job>w.cpu_capacity:pair_reasons.append('同机双方 CPU 申请之和超过节点容量')
                    if sr.ram_gb_per_job+cr.ram_gb_per_job>w.ram_gb_capacity:pair_reasons.append('同机双方内存申请之和超过节点容量')
                if not pair_reasons:return True,[]
                reasons.update(pair_reasons)
        return False,sorted(reasons)

    def pair(self,client,definition,avoid=()):
        if len(self.active)+2>admission.seconds('MAX_ACTIVE_GLOBAL',2):return None
        if sum(self.jobs[r.job_id].owner_id==client.owner_id for r in self.active)+2>admission.seconds('MAX_ACTIVE_PER_USER',2):return None
        server=server_job(definition,client.owner_id)
        for slot in self.slots:
            worker=self.workers.get(slot.worker_id)
            if not worker or not worker.service_network or not self.fits(server,slot):continue
            port=choose_port(self.s,worker,[p for w,p in avoid if w==worker.id])
            if port is None:continue
            if (definition['template'].get('readiness') or {}).get('port','business')!='business' and choose_port(self.s,worker,[port,*[p for w,p in avoid if w==worker.id]]) is None:continue
            request=budget(self.pools[server.pool_id],server)
            reserved=SimpleNamespace(slot_id=slot.id,exclusive=server.gpu_mode=='exclusive',
                reserved_vram_mb=slot.vram_mb if server.gpu_mode=='exclusive' else server.requested_vram_gb*1024,
                reserved_cpu=request.cpu_per_job,reserved_ram_gb=request.ram_gb_per_job,started_at=None)
            target=next((x for x in self.slots if self.fits(client,x,[reserved])),None)
            if target:return slot,target,worker.service_network['address'],port
        return None


def reserve(s,job,slot,at,selection=None,waiting=False,ttl=None):
    pool=s.get(Pool,job.pool_id);request=budget(pool,job)
    run=s.scalar(select(Run).where(Run.job_id==job.id).order_by(Run.attempt.desc()))
    held=list(s.scalars(select(Run).where(Run.slot_id==slot.id,or_(Run.status.in_(admission.ACTIVE),Run.cleanup_confirmed.is_(False)))))
    used={r.allocation_index for r in held}
    run.allocation_index=next(i for i in range(len(used)+1) if i not in used)
    run.slot_id=slot.id;run.queue_name=slot.queue_name;run.execution_id=uid()
    run.exclusive=job.gpu_mode=='exclusive';run.reserved_vram_mb=slot.vram_mb if run.exclusive else job.requested_vram_gb*1024
    run.reserved_cpu=request.cpu_per_job;run.reserved_ram_gb=request.ram_gb_per_job
    run.reserved_at=at;run.waiting_service=waiting
    admission.reservation_deadline(s,run,at,ttl or admission.seconds('RESERVATION_SECONDS',30))
    if selection:
        run.assigned_endpoint=selection['url'];run.assigned_endpoint_mode=selection['mode'];service_dependencies.bind(run,selection)
    job.queued_at=at;admission.transition(s,run,'RESERVED');s.flush()
    return run


def create_reserved_service(s,batch,definition,server_slot,address,port,at,stopped):
    reusable=next((i for i in stopped if i.auto_stopped),None)
    item=reusable or ServiceInstance(resource_sources=definition.get('resource_sources',{}),resources=definition['resources'],name=(batch.title+' / '+definition['key'])[:100],
        template_snapshot=definition['template'],parameters=definition['parameters'],environment=definition['environment'],
        gpu_mode=definition['resources']['gpu_mode'],requested_vram_gb=definition['resources']['requested_vram_gb'],
        orchestration_batch_id=batch.id,service_key=definition['key'])
    item.worker_id=server_slot.worker_id;item.slot_id=server_slot.id;item.address=address;item.port=port;item.auto_stopped=False
    s.add(item);s.flush();services.enqueue(s,item,s.get(User,batch.owner_id))
    server=s.get(Job,item.current_job_id)
    server.client_spec={**server.client_spec,'task_input_version':1}
    server.requested_cpu=definition['resources'].get('cpus');server.requested_ram_gb=definition['resources'].get('ram_gb')
    server.required_gpu_model=definition['resources'].get('required_gpu_model')
    sr=reserve(s,server,server_slot,at)
    return item,server,sr


def allocate_pair(s,job,at):
    orch=s.get(EvaluationOrchestration,job.batch_id)
    if not orch:return None
    planner=Planner(s,at)
    instances=list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==job.batch_id)))
    for definition in orch.definition['services']:
        if definition['key'] not in job.client_spec['service_keys']:continue
        related=[i for i in instances if i.service_key==definition['key']]
        active=[i for i in related if services.held(s,i)]
        if len(active)>=definition['max_instances']:continue
        if definition.get('instance_count') and sum(not i.auto_stopped or services.held(s,i) for i in related)>=definition['instance_count']:continue
        stopped=[i for i in related if not services.held(s,i)]
        # Faults require explicit restart. Only a bind conflict gets bounded,
        # automatic re-placement on a different port (three total attempts).
        faults=[i for i in stopped if not i.auto_stopped]
        avoid=[]
        if faults and not definition.get('instance_count'):
            if len(faults)>=3:continue
            if any('service address is not local or port is already in use' not in (s.get(Job,i.current_job_id).error or '') for i in faults):continue
            avoid=[(i.worker_id,i.port) for i in faults]
        pair=planner.pair(job,definition,avoid)
        if not pair:continue
        server_slot,client_slot,address,port=pair
        item,server,sr=create_reserved_service(s,s.get(EvaluationBatch,job.batch_id),definition,server_slot,address,port,at,stopped)
        selection=service_dependencies.candidate(s,item,definition)
        cr=reserve(s,job,client_slot,at,selection,True,(definition['template'].get('readiness') or definition['template'])['startup_timeout']+admission.seconds('STARTUP_TIMEOUT_SECONDS',120))
        return [(server,server_slot,sr,None),(job,client_slot,cr,selection)]
    return None


def reconcile(s,at):
    from .batch_controls import cancel_job,withdraw
    # An expired unclaimed server reservation must be replanned together with
    # its client, not fall through to standalone service admission.
    for item in s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id.is_not(None))):
        job=s.get(Job,item.current_job_id) if item.current_job_id else None
        waiting=s.scalar(select(Job.id).where(Job.batch_id==item.orchestration_batch_id,Job.status.in_(['PENDING','RESERVED'])).limit(1))
        if job and job.status=='PENDING' and waiting:
            cancel_job(s,job);item.desired_running=False;item.auto_stopped=True
    for run in s.scalars(select(Run).where(Run.status=='RESERVED',Run.waiting_service.is_(True))):
        if service_dependencies.binding_ready(s,run,at):
            run.waiting_service=False
            admission.reservation_deadline(s,run,at,admission.seconds('RESERVATION_SECONDS',30))
        else:
            item=s.get(ServiceInstance,run.assigned_service_instance_id)
            current=service_dependencies.current_run(s,item) if item else None
            if not item or not item.desired_running or not current or current.status in admission.TERMINAL or current.execution_id!=run.assigned_service_execution_id:
                withdraw(s,s.get(Job,run.job_id))
    for orch in s.scalars(select(EvaluationOrchestration)):
        if not orch.stop_after_batch:continue
        jobs=list(s.scalars(select(Job).where(Job.batch_id==orch.batch_id)))
        if not jobs or any(j.status not in admission.TERMINAL for j in jobs):continue
        if s.scalar(select(Run.id).join(Job,Job.id==Run.job_id).where(Job.batch_id==orch.batch_id,Run.cleanup_confirmed.is_(False)).limit(1)):continue
        for item in s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==orch.batch_id)):
            if item.desired_running:
                item.desired_running=False;item.auto_stopped=True;item.revision+=1
                job=s.get(Job,item.current_job_id) if item.current_job_id else None
                if job and job.status not in admission.TERMINAL:cancel_job(s,job)


def _fill_one_instance(s,at,batch,definition,eligible):
    """Attempt one replica; callers rotate only after a real allocation."""
    target=definition['instance_count']
    related=list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==batch.id,ServiceInstance.service_key==definition['key'])))
    occupied=sum(not i.auto_stopped or services.held(s,i) for i in related)
    if occupied>=target:return False
    planner=Planner(s,at)
    if len(planner.active)>=admission.seconds('MAX_ACTIVE_GLOBAL',2):return False
    if sum(planner.jobs[r.job_id].owner_id==batch.owner_id for r in planner.active)>=admission.seconds('MAX_ACTIVE_PER_USER',2):return False
    client_active=any(planner.jobs[r.job_id].id in {j.id for j in eligible} and r.status in admission.ACTIVE for r in planner.active)
    pair=None
    if not client_active:
        # Never fill all capacity with servers while no client can run.
        pair=next((p for j in eligible if (p:=planner.pair(j,definition))),None)
        if not pair:return False
    server=server_job(definition,batch.owner_id)
    allocation=None
    for slot in planner.slots:
        if pair and slot.id!=pair[0].id:continue
        worker=planner.workers.get(slot.worker_id)
        if not worker or not worker.service_network or not planner.fits(server,slot):continue
        port=choose_port(s,worker)
        if port is None:continue
        if (definition['template'].get('readiness') or {}).get('port','business')!='business' and choose_port(s,worker,[port]) is None:continue
        allocation=(slot,worker.service_network['address'],port);break
    if not allocation:return False
    stopped=[i for i in related if i.auto_stopped and not services.held(s,i)]
    create_reserved_service(s,batch,definition,*allocation,at,stopped)
    return True


def fill_desired_instances(s,at):
    """Refill after client admission with an independent persisted owner rotation."""
    queues={}
    for orch in s.scalars(select(EvaluationOrchestration)):
        batch=s.get(EvaluationBatch,orch.batch_id)
        if not batch or batch.deleted_at or batch.scheduling_paused:continue
        owner=s.get(User,batch.owner_id)
        if not owner or not owner.active:continue
        clients=list(s.scalars(select(Job).where(Job.batch_id==batch.id,Job.status.in_(['PENDING','RESERVED','STARTING','RUNNING']))))
        for definition in orch.definition['services']:
            eligible=[j for j in clients if definition['key'] in (j.client_spec or {}).get('service_keys',[])]
            if definition.get('instance_count') and eligible:
                queues.setdefault(owner.id,[]).append((batch,definition,eligible))
    if not queues:return 0
    for candidates in queues.values():
        candidates.sort(key=lambda x:(-x[0].priority,admission.utc(x[0].created_at),x[0].id))
    cursor=s.get(SchedulerState,'service_rotation')
    last=cursor.value.get('owner','') if cursor else ''
    owners=sorted(queues)
    owners=[u for u in owners if u>last]+[u for u in owners if u<=last]
    admitted=0
    while True:
        progressed=False
        for owner in owners:
            for batch,definition,eligible in queues[owner]:
                if not _fill_one_instance(s,at,batch,definition,eligible):continue
                if cursor is None:
                    cursor=SchedulerState(key='service_rotation',value={})
                    s.add(cursor)
                cursor.value={'owner':owner}
                admitted+=1
                progressed=True
                break
        if not progressed:return admitted
