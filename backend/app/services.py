"""Inference services share the evaluation scheduler's resource ledger."""
import ipaddress
from datetime import timedelta
from typing import Literal
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select,or_
from .core import ServiceInstance,ServerTemplate,Worker,Slot,Pool,Job,Run,RunLog,now
from .scheduling import lock,ACTIVE,TERMINAL,utc,online
from .templates import server_view
from .administration import audit
from .batch_controls import cancel_job


class Deploy(BaseModel):
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


def held(s,instance):
    if not instance.current_job_id:return False
    job=s.get(Job,instance.current_job_id)
    return bool(job and (job.status in (*ACTIVE,'PENDING') or s.scalar(select(Run.id).where(Run.job_id==job.id,Run.cleanup_confirmed.is_(False)).limit(1))))


def check_port(s,instance):
    for other in s.scalars(select(ServiceInstance).where(ServiceInstance.worker_id==instance.worker_id,ServiceInstance.port==instance.port,ServiceInstance.id!=instance.id)):
        if held(s,other):raise HTTPException(409,'service port is already reserved on this machine')


def enqueue(s,instance,user):
    slot=s.get(Slot,instance.slot_id);w=s.get(Worker,instance.worker_id);pool=s.get(Pool,slot.pool_id) if slot else None
    if not w or not w.enabled or w.revoked_at or 'server' not in w.roles:raise HTTPException(409,'target machine must be enabled with server role')
    if not slot or not slot.enabled or not pool or not pool.enabled:raise HTTPException(409,'target GPU must belong to an enabled resource pool')
    if instance.gpu_mode=='shared' and (pool.gpu_model=='virtual' or not pool.allow_shared or instance.requested_vram_gb<1):raise HTTPException(400,'shared service requires shared pool and positive VRAM')
    check_port(s,instance)
    definition=instance.template_snapshot
    spec={'workload':'service','contract_version':1,'template':definition,'environment':instance.environment,'parameters':instance.parameters,
          'task_name':instance.name,'seed':0,'endpoint':f"{definition['protocol']}://{instance.address}:{instance.port}",
          'service':{'address':instance.address,'port':instance.port,'container_port':definition['port'],'protocol':definition['protocol'],'health_type':definition['health_type'],'health_path':definition['health_path'],'startup_timeout':definition['startup_timeout']}}
    job=Job(owner_id=user.id,pool_id=pool.id,title='服务: '+instance.name,benchmark='inference-service',model_ref=definition['name'],episodes=1,client_spec=spec,target_slot_id=slot.id,service_instance_id=instance.id,gpu_mode=instance.gpu_mode,requested_vram_gb=instance.requested_vram_gb)
    s.add(job);s.flush();s.add(Run(job_id=job.id,attempt=1));instance.current_job_id=job.id;instance.desired_running=True;instance.health_ready=False;instance.health_at=None;instance.health_error=None


def view(s,item):
    job=s.get(Job,item.current_job_id) if item.current_job_id else None;slot=s.get(Slot,item.slot_id)
    alive=slot is not None and online(slot)
    state=job.status if job else 'STOPPED'
    if job and state=='RUNNING':
        fresh=item.health_at and timedelta(0)<=now()-utc(item.health_at)<timedelta(seconds=15)
        state='HEALTHY' if alive and fresh and item.health_ready else 'UNHEALTHY' if alive and fresh else 'UNKNOWN'
    if not item.desired_running and not held(s,item):state='STOPPED'
    return {**{k:getattr(item,k) for k in ('id','name','worker_id','slot_id','address','port','revision','desired_running','current_job_id','health_at','health_error','created_at')},'state':state,'online':alive,'cleanup_pending':bool(job and s.scalar(select(Run.id).where(Run.job_id==job.id,Run.cleanup_confirmed.is_(False)).limit(1))),'endpoint':f"{item.template_snapshot['protocol']}://{item.address}:{item.port}",'template':item.template_snapshot,'parameters':item.parameters,'error':job.error if job else None}


def install(app,db,admin,worker_auth):
    @app.get('/api/v1/admin/services')
    def listing(user=Depends(admin),s=Depends(db)):
        return [view(s,i) for i in s.scalars(select(ServiceInstance).order_by(ServiceInstance.created_at.desc()))]

    @app.post('/api/v1/admin/services',status_code=201)
    def create(body:Deploy,user=Depends(admin),s=Depends(db)):
        lock(s);t=s.get(ServerTemplate,body.template_id);slot=s.get(Slot,body.slot_id)
        if not t or not t.enabled or t.archived or t.status!='published':raise HTTPException(400,'unknown or disabled server template')
        if not slot:raise HTTPException(404,'GPU slot not found')
        definition=server_view(t);runtime=definition.get('host_runtime') or definition.get('runtime') or {}
        if set(body.environment)-set(runtime.get('overridable_env',[])):raise HTTPException(400,'environment override is not allowed')
        parameters=body.parameters
        try:
            from runner.image_runtime import placeholders
            for value in body.environment.values():
                placeholders(value,runtime.get('assets',{}),runtime.get('mounts',{}))
            if definition.get('parameter_schema') is not None:
                from runner.parameter_contract import resolve_values
                parameters=resolve_values(definition['parameter_schema'],body.parameters,body.parameter_overrides)
            elif body.parameter_overrides: raise ValueError('parameter_overrides requires a typed template')
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc
        item=ServiceInstance(parameters=parameters,name=body.name,worker_id=slot.worker_id,slot_id=slot.id,address=body.address,port=body.port,template_snapshot=definition,environment=body.environment,gpu_mode=body.gpu_mode,requested_vram_gb=body.requested_vram_gb)
        s.add(item);s.flush();enqueue(s,item,user);audit(s,user,'service.deploy',item.id);s.commit();return view(s,item)

    @app.post('/api/v1/admin/services/{identity}/control')
    def control(identity:str,body:Control,user=Depends(admin),s=Depends(db)):
        lock(s);item=s.get(ServiceInstance,identity)
        if not item:raise HTTPException(404,'service not found')
        if item.revision!=body.expected_revision:raise HTTPException(409,'service changed; refresh')
        if body.action=='stop':
            item.desired_running=False
            if item.current_job_id:cancel_job(s,s.get(Job,item.current_job_id))
        else:
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
        chunks=list(s.scalars(select(RunLog).where(RunLog.run_id==run.id).order_by(RunLog.sequence))) if run else []
        return {'job_id':chosen,'history':[{'job_id':j.id,'status':j.status,'created_at':j.created_at} for j in jobs],'content':''.join(x.content for x in chunks)}

    @app.post('/api/v1/worker/executions/{execution}/service-health')
    def health(execution:str,body:Health,worker=Depends(worker_auth),s=Depends(db)):
        lock(s);run=s.scalar(select(Run).where(Run.execution_id==execution))
        slot=s.get(Slot,run.slot_id) if run and run.slot_id else None
        if not slot or slot.worker_id!=worker.id:raise HTTPException(404,'execution not found')
        if run.status not in ('STARTING','RUNNING','CANCEL_REQUESTED') or not run.lease_expires_at or utc(run.lease_expires_at)<=now():raise HTTPException(409,'execution lease is no longer valid')
        job=s.get(Job,run.job_id)
        item=s.get(ServiceInstance,job.service_instance_id) if job and job.service_instance_id else None
        if not item or item.current_job_id!=job.id:raise HTTPException(409,'service execution no longer current')
        item.health_at=now();item.health_ready=body.ready;item.health_error=body.error;s.commit();return {'accepted':True}
