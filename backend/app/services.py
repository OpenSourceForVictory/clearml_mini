"""Inference services share the evaluation scheduler's resource ledger."""
import ipaddress
from datetime import timedelta
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select,or_
from .core import ServiceInstance,ServerTemplate,Worker,Slot,Pool,Job,Run,RunLog,RunLogState,RunLogChunk,now,uid
from .scheduling import lock,ACTIVE,TERMINAL,utc,online
from .templates import server_view
from .administration import audit
from .batch_controls import cancel_job


from .job_resources import Request as ResourceRequest

class Deploy(BaseModel):
    resources:ResourceRequest=Field(default_factory=ResourceRequest)
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    template_id:str
    slot_id:str
    address:str
    port:int=Field(ge=1024,le=65535)
    environment:dict[str,str]=Field(default_factory=dict)
    parameters:dict=Field(default_factory=dict)
    parameter_overrides:dict=Field(default_factory=dict)
    gpu_mode:Literal['exclusive','shared']='exclusive'
    requested_vram_gb:int=Field(default=0,ge=0,le=10000)
    @field_validator('address')
    @classmethod
    def host(cls,v):
        ip=ipaddress.ip_address(v)
        if ip.version!=4 or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_loopback:raise ValueError('use an explicit reachable machine IPv4 address')
        return str(ip)
    @field_validator('parameters','parameter_overrides')
    @classmethod
    def bounded_parameters(cls,v):
        from runner.parameter_contract import encoded
        encoded(v)
        return v
    @field_validator('environment')
    @classmethod
    def env(cls,v):
        from runner.image_runtime import environment
        return environment(v)


class Control(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['start','stop']
    expected_revision:int=Field(ge=0)


class Health(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ready:bool
    error:str|None=Field(default=None,max_length=500)
    epoch:str|None=Field(default=None,max_length=36)
    sequence:int=Field(default=0,ge=0)
    sample_age_ms:int=Field(default=0,ge=0,le=1200000)
    duration_ms:int=Field(default=0,ge=0,le=120000)
    phase:Literal['configuration','connect','handshake','response','message']|None=None
    error_code:Literal['redirect_rejected','unexpected_status','authentication_failed','response_too_large','response_mismatch','subprotocol_mismatch','connection_closed','invalid_message','tls_failed','handshake_failed','timeout','configuration_invalid','connection_failed']|None=None


def held(s,instance):
    if not instance.current_job_id:return False
    job=s.get(Job,instance.current_job_id)
    return bool(job and (job.status in (*ACTIVE,'PENDING') or s.scalar(select(Run.id).where(Run.job_id==job.id,Run.cleanup_confirmed.is_(False)).limit(1))))


def ports(instance):
    return {p for p in (instance.port, instance.health_port) if p is not None}


def check_port(s,instance):
    for other in s.scalars(select(ServiceInstance).where(ServiceInstance.worker_id==instance.worker_id,ServiceInstance.id!=instance.id)):
        if ports(instance) & ports(other) and held(s,other):raise HTTPException(409,'service port is already reserved on this machine')


def enqueue(s,instance,user):
    slot=s.get(Slot,instance.slot_id);w=s.get(Worker,instance.worker_id);pool=s.get(Pool,slot.pool_id) if slot else None
    if not w or not w.enabled or w.revoked_at or 'server' not in w.roles:raise HTTPException(409,'target machine must be enabled with server role')
    if not slot or not slot.enabled or not pool or not pool.enabled:raise HTTPException(409,'target GPU must belong to an enabled resource pool')
    if instance.gpu_mode=='shared' and (pool.gpu_model=='virtual' or not pool.allow_shared or instance.requested_vram_gb<1):raise HTTPException(400,'shared service requires shared pool and positive VRAM')
    definition=instance.template_snapshot
    if definition.get('readiness'):
        from .service_network import choose_port
        health=definition['readiness']
        instance.health_port = None
        if health['port'] != 'business':
            chosen=choose_port(s,w,[instance.port])
            if chosen is None:raise HTTPException(409,'no independent health port available in machine service network')
            instance.health_port=chosen
    check_port(s,instance)
    resource=instance.resources or {'gpu_mode':instance.gpu_mode,'requested_vram_gb':instance.requested_vram_gb}
    from .job_resources import validate
    validate(pool,resource)
    network=w.service_network or {}
    bind_address=instance.address
    if (network.get('address')==instance.address and
            network.get('port_start',19000)<=instance.port<=network.get('port_end',19100)):
        bind_address=network.get('bind_address') or instance.address
    spec={'resource_sources':instance.resource_sources or {},'resources':resource,'workload':'service','contract_version':1,'template':definition,'environment':instance.environment,'parameters':instance.parameters,
          'task_name':instance.name,'seed':0,'endpoint':f"{definition['protocol']}://{instance.address}:{instance.port}",
          'service':{'address':bind_address,'port':instance.port,'container_port':definition['port'],'protocol':definition['protocol'],'health_type':definition['health_type'],'health_path':definition['health_path'],'startup_timeout':definition['startup_timeout']}}
    if definition.get('readiness'):
        spec['service'].update(readiness=definition['readiness'],health_port=instance.health_port or instance.port)
    job=Job(owner_id=user.id,pool_id=pool.id,title='服务: '+instance.name,benchmark='inference-service',model_ref=definition['name'],episodes=1,client_spec=spec,target_slot_id=slot.id,service_instance_id=instance.id,gpu_mode=instance.gpu_mode,requested_vram_gb=instance.requested_vram_gb,requested_cpu=resource.get('cpus'),requested_ram_gb=resource.get('ram_gb'),required_gpu_model=resource.get('required_gpu_model'))
    s.add(job);s.flush();s.add(Run(job_id=job.id,attempt=1));instance.current_job_id=job.id;instance.desired_running=True;instance.health_ready=False;instance.health_at=None;instance.health_error=None;instance.probe_state={}


def view(s,item,loaded=None):
    job=(loaded['jobs'].get(item.current_job_id) if loaded is not None else s.get(Job,item.current_job_id)) if item.current_job_id else None
    slot=loaded['slots'].get(item.slot_id) if loaded is not None else s.get(Slot,item.slot_id)
    cleanup=bool(job and (job.id in loaded['cleanup'] if loaded is not None else s.scalar(select(Run.id).where(Run.job_id==job.id,Run.cleanup_confirmed.is_(False)).limit(1))))
    alive=slot is not None and online(slot)
    state=job.status if job else 'STOPPED'
    if job and state=='RUNNING':
        from .service_readiness import fresh as is_fresh
        fresh=is_fresh(item)
        state='HEALTHY' if alive and fresh and item.health_ready else 'UNHEALTHY' if alive and fresh else 'UNKNOWN'
        if item.template_snapshot.get('readiness') and alive and fresh and not (item.probe_state or {}).get('ever_ready'):state='STARTING'
    if not item.desired_running and not (job and (job.status in (*ACTIVE,'PENDING') or cleanup)):state='STOPPED'
    from .service_readiness import diagnostics
    from .placement import view as placement_view
    run = job.runs[-1] if job and job.runs else None
    assigned = (loaded['slots'].get(run.slot_id) if loaded is not None else s.get(Slot, run.slot_id)) if run and run.slot_id else None
    return {'placement':placement_view(assigned),'probe':diagnostics(item),'health_port':item.health_port,**{k:getattr(item,k) for k in ('id','name','worker_id','slot_id','address','port','revision','desired_running','current_job_id','health_at','health_error','created_at')},'state':state,'online':alive,'cleanup_pending':cleanup,'endpoint':f"{item.template_snapshot['protocol']}://{item.address}:{item.port}",'template':item.template_snapshot,'parameters':item.parameters,'resources':item.resources,'resource_sources':item.resource_sources or {},'error':job.error if job else None}


def resolve_parameters(definition,parameters,environment,overrides):
    runtime=definition.get('host_runtime') or definition.get('runtime') or {}
    if set(environment)-set(runtime.get('overridable_env',[])):raise HTTPException(400,'environment override is not allowed')
    try:
        from runner.image_runtime import placeholders
        for value in environment.values():
            placeholders(value,runtime.get('assets',{}),runtime.get('mounts',{}))
        if definition.get('parameter_schema') is not None:
            from runner.parameter_contract import resolve_values
            parameters=resolve_values(definition['parameter_schema'],parameters,overrides)
        elif overrides: raise ValueError('parameter_overrides requires a typed template')
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    return parameters


def create_instance(s,body,user):
    t=s.get(ServerTemplate,body.template_id);slot=s.get(Slot,body.slot_id)
    if not t or not t.enabled or t.archived or t.status!='published':raise HTTPException(400,'unknown or disabled server template')
    if not slot:raise HTTPException(404,'GPU slot not found')
    definition=server_view(t)
    parameters=resolve_parameters(definition,body.parameters,body.environment,body.parameter_overrides)
    from .job_resources import resolve as resolve_resources,explicit,submission
    pool=s.get(Pool,slot.pool_id)
    if not pool:raise HTTPException(400,'target device requires a resource pool')
    resource,sources=resolve_resources(pool,definition,('submission',submission(body)))
    item=ServiceInstance(resource_sources=sources,resources=resource,parameters=parameters,name=body.name,worker_id=slot.worker_id,slot_id=slot.id,address=body.address,port=body.port,template_snapshot=definition,environment=body.environment,gpu_mode=resource['gpu_mode'],requested_vram_gb=resource['requested_vram_gb'])
    s.add(item);s.flush();enqueue(s,item,user);audit(s,user,'service.deploy',item.id);return item


def install(app,db,admin,worker_auth):
    @app.get('/api/v1/admin/services')
    def listing(user=Depends(admin),s=Depends(db)):
        return [view(s,i) for i in s.scalars(select(ServiceInstance).order_by(ServiceInstance.created_at.desc()))]

    @app.post('/api/v1/admin/services',status_code=201)
    def create(body:Deploy,user=Depends(admin),s=Depends(db)):
        lock(s);item=create_instance(s,body,user);s.commit();return view(s,item)

    @app.post('/api/v1/admin/services/{identity}/control')
    def control(identity:str,body:Control,user=Depends(admin),s=Depends(db)):
        lock(s);item=s.get(ServiceInstance,identity)
        if not item:raise HTTPException(404,'service not found')
        if item.revision!=body.expected_revision:raise HTTPException(409,'service changed; refresh')
        if body.action=='stop':
            item.desired_running=False
            job=s.get(Job,item.current_job_id) if item.current_job_id else None
            if job and job.status not in TERMINAL:cancel_job(s,job)
        else:
            from .batch_deletion import assert_live_batch
            assert_live_batch(s, item.orchestration_batch_id)
            if held(s,item):raise HTTPException(409,'stop current service and wait for cleanup before starting')
            template=s.get(ServerTemplate,item.template_snapshot['id'])
            if not template or not template.enabled or template.archived:raise HTTPException(409,'service template is disabled or archived')
            enqueue(s,item,user)
        item.revision+=1;audit(s,user,'service.'+body.action,item.id);s.commit();return view(s,item)

    @app.get('/api/v1/admin/services/{identity}/logs')
    def logs(identity:str,job_id:str|None=None,user=Depends(admin),s=Depends(db)):
        item=s.get(ServiceInstance,identity)
        if not item:raise HTTPException(404,'service not found')
        jobs=list(s.scalars(select(Job).where(Job.service_instance_id==identity).order_by(Job.created_at.desc())))
        chosen=job_id or item.current_job_id
        if chosen not in [j.id for j in jobs]:raise HTTPException(404,'service execution not found')
        run=s.scalar(select(Run).where(Run.job_id==chosen).order_by(Run.attempt.desc()))
        # Match the job log reader: a v2 state is authoritative, even if its window is empty.
        model=RunLogChunk if run and s.get(RunLogState,run.id) is not None else RunLog
        chunks=list(s.scalars(select(model).where(model.run_id==run.id).order_by(model.sequence))) if run else []
        return {'job_id':chosen,'history':[{'job_id':j.id,'status':j.status,'created_at':j.created_at} for j in jobs],'content':''.join(x.content for x in chunks)}

    @app.post('/api/v1/worker/executions/{execution}/service-probe-session')
    def session(execution:str,worker=Depends(worker_auth),s=Depends(db)):
        from .scheduling import fast_lock
        fast_lock(s)
        run=s.scalar(select(Run).where(Run.execution_id==execution))
        slot=s.get(Slot,run.slot_id) if run and run.slot_id else None
        if not slot or slot.worker_id!=worker.id:raise HTTPException(404,'execution not found')
        if run.status not in ('STARTING','RUNNING') or not run.lease_expires_at or utc(run.lease_expires_at)<=now():raise HTTPException(409,'invalid execution lease')
        job=s.get(Job,run.job_id);item=s.get(ServiceInstance,job.service_instance_id) if job.service_instance_id else None
        if not item or item.current_job_id!=job.id:raise HTTPException(409,'service execution no longer current')
        epoch=uid()
        item.probe_state={**(item.probe_state or {}),'startup_at':(item.probe_state or {}).get('startup_at',now().isoformat()),'epoch':epoch,'sequence':0,'successes':0,'failures':0}
        item.health_ready=False;item.health_at=None
        s.commit();return {'epoch':epoch,'ever_ready':bool(item.probe_state.get('ever_ready'))}

    @app.post('/api/v1/worker/executions/{execution}/service-health')
    def health(execution:str,body:Health,worker=Depends(worker_auth),s=Depends(db)):
        run=s.scalar(select(Run).where(Run.execution_id==execution))
        slot=s.get(Slot,run.slot_id) if run and run.slot_id else None
        if not slot or slot.worker_id!=worker.id:raise HTTPException(404,'execution not found')
        if run.status not in ('STARTING','RUNNING','CANCEL_REQUESTED') or not run.lease_expires_at or utc(run.lease_expires_at)<=now():raise HTTPException(409,'execution lease is no longer valid')
        job=s.get(Job,run.job_id)
        item=s.get(ServiceInstance,job.service_instance_id) if job and job.service_instance_id else None
        if not item or item.current_job_id!=job.id:raise HTTPException(409,'service execution no longer current')
        s.refresh(item, with_for_update=True)
        if item.current_job_id!=job.id:raise HTTPException(409,'service execution no longer current')
        if item.template_snapshot.get('readiness'):
            from .service_readiness import accept
            outcome=accept(item,body)
            if outcome=='epoch':raise HTTPException(409,'probe generation is no longer current')
            s.commit();return {'accepted':outcome=='accepted','ready':item.health_ready}
        item.health_at=now();item.health_ready=body.ready;item.health_error=body.error;s.commit();return {'accepted':True,'ready':item.health_ready}
