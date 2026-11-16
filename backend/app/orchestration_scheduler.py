"""Managed service dependencies using the existing Run reservation ledger.

A waiting client's RESERVED Run is its startup budget, never a second resource
counter. It cannot be claimed before the bound service generation is healthy.
"""
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy import select,or_
from .core import EvaluationOrchestration,EvaluationBatch,ServiceInstance,Worker,Slot,Pool,Job,Run,User,now,uid
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
        self.workers={x.id:x for x in s.scalars(select(Worker))}
        self.slots=list(s.scalars(select(Slot).order_by(Slot.node_name,Slot.gpu_index)))
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
        """Ignore occupancy/liveness; reject only a known capacity contradiction.

        A missing role/pool is environment preparation, not proof of impossibility.
        """
        server=server_job(definition,client.owner_id)
        def targets(job):
            role='server' if job.benchmark=='inference-service' else 'client'
            return [x for x in self.slots if x.pool_id==job.pool_id and x.worker_id in self.workers
                    and role in (self.workers[x.worker_id].roles or ['client'])
                    and (not getattr(job,'target_slot_id',None) or job.target_slot_id==x.id)]
        servers,clients=targets(server),targets(client)
        if not servers or not clients:return None
        def fits(job,slot):
            p=self.pools[job.pool_id];w=self.workers[slot.worker_id];r=budget(p,job)
            return (not job.required_gpu_model or slot.gpu_model==job.required_gpu_model) and r.cpu_per_job<=w.cpu_capacity and r.ram_gb_per_job<=w.ram_gb_capacity and (not job.requested_vram_gb or job.requested_vram_gb*1024<=slot.vram_mb-p.vram_reserve_gb*1024)
        for ss in servers:
            for cs in clients:
                if not fits(server,ss) or not fits(client,cs):continue
                if ss.id==cs.id:
                    p=self.pools[server.pool_id]
                    if server.gpu_mode!='shared' or client.gpu_mode!='shared' or p.max_tasks_per_gpu<2:continue
                    if (server.requested_vram_gb+client.requested_vram_gb+p.vram_reserve_gb)*1024>ss.vram_mb:continue
                if ss.worker_id==cs.worker_id:
                    w=self.workers[ss.worker_id];sr=budget(self.pools[server.pool_id],server);cr=budget(self.pools[client.pool_id],client)
                    if w.max_concurrent<2 or sr.cpu_per_job+cr.cpu_per_job>w.cpu_capacity or sr.ram_gb_per_job+cr.ram_gb_per_job>w.ram_gb_capacity:continue
                return True
        return False

    def pair(self,client,definition,avoid=()):
        if len(self.active)+2>admission.seconds('MAX_ACTIVE_GLOBAL',2):return None
        if sum(self.jobs[r.job_id].owner_id==client.owner_id for r in self.active)+2>admission.seconds('MAX_ACTIVE_PER_USER',2):return None
        server=server_job(definition,client.owner_id)
        for slot in self.slots:
            worker=self.workers.get(slot.worker_id)
            if not worker or not worker.service_network or not self.fits(server,slot):continue
            port=choose_port(self.s,worker,[p for w,p in avoid if w==worker.id])
            if port is None:continue
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
    run.reserved_at=at;run.reservation_expires_at=at+timedelta(seconds=ttl or admission.seconds('RESERVATION_SECONDS',30));run.waiting_service=waiting
    if selection:
        run.assigned_endpoint=selection['url'];run.assigned_endpoint_mode=selection['mode'];service_dependencies.bind(run,selection)
    job.queued_at=at;admission.transition(s,run,'RESERVED');s.flush()
    return run


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
        stopped=[i for i in related if not services.held(s,i)]
        # Faults require explicit restart. Only a bind conflict gets bounded,
        # automatic re-placement on a different port (three total attempts).
        faults=[i for i in stopped if not i.auto_stopped]
        avoid=[]
        if faults:
            if len(faults)>=3:continue
            if any('service address is not local or port is already in use' not in (s.get(Job,i.current_job_id).error or '') for i in faults):continue
            avoid=[(i.worker_id,i.port) for i in faults]
        pair=planner.pair(job,definition,avoid)
        if not pair:continue
        server_slot,client_slot,address,port=pair
        reusable=next((i for i in stopped if i.auto_stopped),None)
        item=reusable or ServiceInstance(resource_sources=definition.get('resource_sources',{}),resources=definition['resources'],name=(s.get(EvaluationBatch,job.batch_id).title+' / '+definition['key'])[:100],
            template_snapshot=definition['template'],parameters=definition['parameters'],environment=definition['environment'],
            gpu_mode=definition['resources']['gpu_mode'],requested_vram_gb=definition['resources']['requested_vram_gb'],
            orchestration_batch_id=job.batch_id,service_key=definition['key'])
        item.worker_id=server_slot.worker_id;item.slot_id=server_slot.id;item.address=address;item.port=port;item.auto_stopped=False
        s.add(item);s.flush();services.enqueue(s,item,s.get(User,job.owner_id))
        server=s.get(Job,item.current_job_id)
        server.client_spec={**server.client_spec,'task_input_version':1}
        server.requested_cpu=definition['resources'].get('cpus');server.requested_ram_gb=definition['resources'].get('ram_gb')
        server.required_gpu_model=definition['resources'].get('required_gpu_model')
        sr=reserve(s,server,server_slot,at)
        selection=service_dependencies.candidate(s,item,definition)
        cr=reserve(s,job,client_slot,at,selection,True,definition['template']['startup_timeout']+admission.seconds('STARTUP_TIMEOUT_SECONDS',120))
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
            run.reservation_expires_at=at+timedelta(seconds=admission.seconds('RESERVATION_SECONDS',30))
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
                if item.current_job_id:cancel_job(s,s.get(Job,item.current_job_id))
