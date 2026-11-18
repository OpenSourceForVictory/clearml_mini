"""Owner-scoped orchestration commands; submission owns one transaction."""
import hashlib
import json
from typing import Literal
from fastapi import Depends,Header,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator
from sqlalchemy import select
from .core import EvaluationOrchestration,EvaluationBatch,EvaluationSuite,ServerTemplate,Job,Run,ServiceInstance,Pool,User,RunLog
from .batches import BatchSubmit,SuiteCreate,resolve_batch,persist_batch,batch_view
from .scheduling import lock,seconds
from .job_resources import Request,validate as validate_resources
from . import services
from .templates import server_view
from .template_versions import visible


class ServiceDefinition(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:str=Field(default='default',pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
    template_id:str
    pool_id:str
    slot_id:str|None=None
    mode:Literal['exclusive','shared']='shared'
    max_instances:int|None=Field(default=None,ge=1,le=20)
    instance_count:int|None=Field(default=None,ge=1,le=20)

    @model_validator(mode='after')
    def count_mode(self):
        if self.instance_count is not None and self.max_instances is not None:
            raise ValueError('instance_count and legacy max_instances are mutually exclusive')
        return self
    parameters:dict=Field(default_factory=dict)
    parameter_overrides:dict=Field(default_factory=dict)
    environment:dict[str,str]=Field(default_factory=dict)
    resources:Request=Field(default_factory=Request)

    @field_validator('environment')
    @classmethod
    def env(cls,value):
        from runner.image_runtime import environment
        return environment(value)

    @field_validator('parameters','parameter_overrides')
    @classmethod
    def bounded(cls,value):
        from runner.parameter_contract import encoded
        encoded(value);return value


class Submit(BatchSubmit):
    new_suite:SuiteCreate|None=None
    services:list[ServiceDefinition]=Field(default_factory=list,max_length=20)
    stop_after_batch:bool=True

    @model_validator(mode='after')
    def exclusive_sources(self):
        if self.new_suite and (self.suite_id or self.cases):raise ValueError('choose exactly one suite source')
        if self.new_suite and self.client.template_id not in self.new_suite.template_ids:raise ValueError('new suite must use selected client template')
        if (self.client.service_mode=='managed')!=bool(self.services):raise ValueError('managed mode requires service definitions; other modes cannot deploy services')
        if len({x.key for x in self.services})!=len(self.services):raise ValueError('duplicate logical service key')
        return self


def digest(body):
    return hashlib.sha256(json.dumps(body.model_dump(),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def compile_submission(s,body,user):
    request=body
    if body.new_suite:
        from .suites import validate,SuiteWrite
        validate(s,SuiteWrite(**body.new_suite.model_dump()),user)
        request=body.model_copy(update={'cases':body.new_suite.cases})
    pool,cases,configs=resolve_batch(s,request,user,body.new_suite.client_defaults if body.new_suite else None)
    definitions=[]
    for item in body.services:
        template=s.get(ServerTemplate,item.template_id);server_pool=s.get(Pool,item.pool_id)
        if not template or not visible(template,user) or not template.enabled or template.archived or template.status!='published':
            raise HTTPException(400,'unknown or unavailable server template')
        if not server_pool or not server_pool.enabled:raise HTTPException(400,'unknown or disabled server resource pool')
        definition=server_view(template)
        if any(definition['protocol'] not in spec['template']['protocols'] for spec in configs if not spec.get('service_keys') or item.key in spec['service_keys']):
            raise HTTPException(400,'server protocol does not match client template')
        values=services.resolve_parameters(definition,item.parameters,item.environment,item.parameter_overrides)
        from .job_resources import resolve as resolve_resources,explicit
        resource,resource_sources=resolve_resources(server_pool,definition,('submission',explicit(item.resources)))
        definitions.append({**item.model_dump(exclude={'parameter_overrides','resources'}),'parameters':values,'template':definition,
                            'resources':resource,'resource_sources':resource_sources,'max_instances':item.instance_count or item.max_instances or (1 if item.mode=='shared' else min(body.max_concurrent,20))})
    keys={d['key'] for d in definitions}
    for spec in configs:
        spec['task_input_version']=1
        if spec['service_keys'] and (body.client.service_mode!='managed' or set(spec['service_keys'])-keys):
            raise HTTPException(400,'task references an unknown managed service')
        if body.client.service_mode=='managed' and not spec['service_keys']:spec['service_keys']=sorted(keys)
    if definitions and (seconds('MAX_ACTIVE_GLOBAL',2)<2 or seconds('MAX_ACTIVE_PER_USER',2)<2):
        raise HTTPException(400,'managed client/server workloads require budget for at least two concurrent executions')
    if definitions:
        from .orchestration_scheduler import Planner
        from types import SimpleNamespace
        from .core import now,Slot
        planner=Planner(s,now())
        for definition in definitions:
            if definition.get('slot_id'):
                slot=s.get(Slot,definition['slot_id'])
                if not slot or slot.pool_id!=definition['pool_id']:raise HTTPException(400,'fixed server slot must belong to its resource pool')
        for case,spec in zip(cases,configs):
            r=spec['resources']
            client=SimpleNamespace(owner_id=user.id,pool_id=pool.id,benchmark='cs-client',client_spec=spec,
                gpu_mode=r['gpu_mode'],requested_vram_gb=r['requested_vram_gb'],required_gpu_model=r.get('required_gpu_model'),
                requested_cpu=r.get('cpus'),requested_ram_gb=r.get('ram_gb'))
            possible=[(d,planner.structural_pair_diagnosis(client,d)) for d in definitions if d['key'] in spec['service_keys']]
            if possible and all(result is False for _,(result,_) in possible):
                # Include only the selected pool names and configured constraints.
                details=[]
                for definition,(_,reasons) in possible:
                    server_pool=planner.pools[definition['pool_id']]
                    details.append('服务 '+definition['key']+'（客户端池 '+pool.name+'；服务端池 '+server_pool.name+'）：'+'、'.join(reasons))
                raise HTTPException(400,case.case_id+'：客户端与服务端无法同时分配；'+'；'.join(details))
    return request,pool,cases,configs,definitions


def owned(s,identity,user):
    batch=s.get(EvaluationBatch,identity);orch=s.get(EvaluationOrchestration,identity)
    if not batch or batch.deleted_at or not orch or (batch.owner_id!=user.id and user.role!='admin'):raise HTTPException(404,'orchestration not found')
    return batch,orch


def diagnose_service(s, definition, owner, context, planner, batch=None):
    """Read-only per-device admission and network diagnosis for a managed service."""
    from .orchestration_scheduler import server_job
    from .service_network import choose_port
    pool=planner.pools.get(definition['pool_id'])
    diagnosis=context.inspect(server_job(definition,owner.id),owner,pool,batch)
    diagnosis['nodes']=[node for node in diagnosis['nodes'] if 'server' in (planner.workers[context.slots[node['slot_id']].worker_id].roles or ['client'])]
    if not diagnosis['nodes'] and not any(r['code']=='no_nodes' for r in diagnosis['reasons']):
        diagnosis['reasons'].append({'code':'node_role_mismatch','category':'environment',
                                     'message':'资源池中没有具备服务端角色的设备','action':'请管理员检查机器角色和资源池成员'})
    for node in diagnosis['nodes']:
        if not node['ready']:continue
        slot=context.slots[node['slot_id']]
        worker=planner.workers.get(slot.worker_id)
        if not worker or not worker.service_network:
            node['reasons'].append({'code':'service_network_pending','category':'environment',
                                    'message':'服务端网络地址或端口范围未配置','action':'请管理员配置服务端网络'})
        else:
            port=choose_port(s,worker)
            separate_health=(definition['template'].get('readiness') or {}).get('port','business')!='business'
            if port is None or (separate_health and choose_port(s,worker,[port]) is None):
                node['reasons'].append({'code':'service_port_busy','category':'resource',
                                        'message':'服务端端口范围暂无可用端口','action':'等待已有服务释放端口或调整端口范围'})
        node['ready']=not node['reasons'] and not diagnosis['reasons']
    if not any(node['ready'] for node in diagnosis['nodes']):
        all_reasons=diagnosis['reasons']+[r for node in diagnosis['nodes'] for r in node['reasons']]
        diagnosis['status']='waiting_resources' if all_reasons and all(r['category']=='resource' for r in all_reasons) else 'needs_environment'
    return {'key':definition['key'],'pool_name':pool.name if pool else '资源池不可用',
            'status':diagnosis['status'],'reasons':diagnosis['reasons'],'nodes':diagnosis['nodes']}


def view(s,batch):
    result=batch_view(s,batch);orch=s.get(EvaluationOrchestration,batch.id)
    instances=list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==batch.id)))
    scheduling=[]
    if orch.definition['service_mode']=='managed':
        from .preflight import DiagnosticContext
        from .orchestration_scheduler import Planner
        context=DiagnosticContext(s)
        planner=Planner(s,context.at)
        owner=s.get(User,batch.owner_id)
        for definition in orch.definition['services']:
            related=[item for item in instances if item.service_key==definition['key']]
            occupied=sum(not item.auto_stopped or services.held(s,item) for item in related)
            if occupied >= (definition.get('instance_count') or 1):continue
            scheduling.append(diagnose_service(s,definition,owner,context,planner,batch))
    result['orchestration']={'stop_after_batch':orch.stop_after_batch,'services':[
        {**services.view(s,i),'service_key':i.service_key,'auto_stopped':i.auto_stopped}
        for i in instances],
        'definitions':orch.definition['services'],'service_mode':orch.definition['service_mode'],
        'scheduling':scheduling}
    return result


def install(app,db,current):
    @app.post('/api/v1/orchestrations/preflight')
    def preflight(body:Submit,user=Depends(current),s=Depends(db)):
        request,pool,cases,configs,definitions=compile_submission(s,body,user)
        from .preflight import inspect_batch
        result=inspect_batch(s,request,user,pool,cases,configs)
        result['service_plan']=[{'key':d['key'],'template':d['template'],'mode':d['mode'],'pool_id':d['pool_id'],'resources':d['resources'],'resource_sources':d.get('resource_sources',{}),'address':'automatic','max_instances':d['max_instances'],'instance_count':d.get('instance_count')} for d in definitions]
        result['effective_parameters']=[{'case_id':c.case_id,'parameters':spec['parameters'],'sources':spec.get('parameter_sources',{})} for c,spec in zip(cases,configs)]
        if definitions:
            from .orchestration_scheduler import Planner
            from .preflight import DiagnosticContext
            from types import SimpleNamespace
            from .core import now
            planner=Planner(s,now());checks=[]
            context=DiagnosticContext(s)
            result['server_checks']=[diagnose_service(s,d,user,context,planner) for d in definitions]
            for case,spec in zip(cases,configs):
                r=spec['resources'];client=SimpleNamespace(owner_id=user.id,pool_id=pool.id,benchmark='cs-client',client_spec=spec,
                    gpu_mode=r['gpu_mode'],requested_vram_gb=r['requested_vram_gb'],required_gpu_model=r.get('required_gpu_model'),
                    requested_cpu=r.get('cpus'),requested_ram_gb=r.get('ram_gb'))
                fit=next((pair for d in definitions if d['key'] in spec['service_keys'] and (pair:=planner.pair(client,d))),None)
                checks.append({'case_id':case.case_id,'pair_ready':bool(fit)})
            result['pair_checks']=checks
            result['status']=('needs_environment' if result['status']=='needs_environment' or any(x['status']=='needs_environment' for x in result['server_checks']) else
                              'ready' if any(x['pair_ready'] for x in checks) and any(x['status']=='ready' for x in result['server_checks']) else
                              'waiting_resources')
            result['note']='平台自动选择两端节点和端口，联合预留资源后启动服务；健康后自动向客户端注入地址。预检不预留资源。'
        return result

    @app.post('/api/v1/orchestrations',status_code=201)
    def submit(body:Submit,idempotency_key:str=Header(...),user=Depends(current),s=Depends(db)):
        if not idempotency_key.strip() or len(idempotency_key)>200:raise HTTPException(400,'invalid idempotency key')
        lock(s);s.execute(select(User.id).where(User.id==user.id).with_for_update())
        fingerprint=digest(body)
        old=s.scalar(select(EvaluationBatch).where(EvaluationBatch.owner_id==user.id,EvaluationBatch.idempotency_key==idempotency_key))
        if old:
            if old.deleted_at:raise HTTPException(409,'batch has been deleted')
            orch=s.get(EvaluationOrchestration,old.id)
            if not orch or orch.request_digest!=fingerprint:raise HTTPException(409,'idempotency key already used for a different submission')
            return view(s,old)
        request,pool,cases,configs,definitions=compile_submission(s,body,user)
        if body.new_suite:
            suite=EvaluationSuite(owner_id=user.id,**body.new_suite.model_dump());s.add(suite);s.flush()
            request=request.model_copy(update={'suite_id':suite.id})
        batch=persist_batch(s,request,user,pool,cases,configs,idempotency_key)
        for j in s.scalars(select(Job).where(Job.batch_id==batch.id)):
            j.client_spec={**j.client_spec,'orchestration_batch_id':batch.id}
        s.add(EvaluationOrchestration(batch_id=batch.id,request_digest=fingerprint,
            definition={'services':definitions,'service_mode':body.client.service_mode},stop_after_batch=body.stop_after_batch))
        s.commit();return view(s,batch)

    @app.get('/api/v1/orchestrations/{identity}')
    def detail(identity:str,user=Depends(current),s=Depends(db)):
        batch,_=owned(s,identity,user);return view(s,batch)

    @app.get('/api/v1/orchestrations/{identity}/services/{service_id}/logs')
    def logs(identity:str,service_id:str,job_id:str|None=None,user=Depends(current),s=Depends(db)):
        owned(s,identity,user);item=s.get(ServiceInstance,service_id)
        if not item or item.orchestration_batch_id!=identity:raise HTTPException(404,'service not found')
        job=s.get(Job,job_id or item.current_job_id)
        if not job or job.service_instance_id!=item.id:raise HTTPException(404,'service execution not found')
        run=s.scalar(select(Run).where(Run.job_id==job.id).order_by(Run.attempt.desc()))
        chunks=s.scalars(select(RunLog).where(RunLog.run_id==run.id).order_by(RunLog.sequence)) if run else []
        return {'job_id':job.id,'content':''.join(x.content for x in chunks)}

    @app.post('/api/v1/orchestrations/{identity}/services/{service_id}/control')
    def control(identity:str,service_id:str,body:services.Control,user=Depends(current),s=Depends(db)):
        lock(s);batch,orch=owned(s,identity,user);item=s.get(ServiceInstance,service_id)
        if not item or item.orchestration_batch_id!=identity:raise HTTPException(404,'service not found')
        if item.revision!=body.expected_revision:raise HTTPException(409,'service changed; refresh')
        if body.action=='stop':
            item.desired_running=False;item.auto_stopped=False
            if item.current_job_id:
                from .batch_controls import cancel_job
                cancel_job(s,s.get(Job,item.current_job_id))
        else:
            if services.held(s,item):raise HTTPException(409,'wait for current service cleanup before restarting')
            # Explicit restart permits the dependency scheduler to reserve a
            # client/server pair before recreating this instance's execution.
            pending=s.scalar(select(Job.id).where(Job.batch_id==identity,Job.status.in_(['PENDING','RESERVED'])).limit(1))
            if not pending:
                if orch.stop_after_batch:raise HTTPException(409,'retry a client task before restarting an automatically stopped service')
                services.enqueue(s,item,s.get(User,batch.owner_id))
                definition=next(d for d in orch.definition['services'] if d['key']==item.service_key)
                job=s.get(Job,item.current_job_id)
                job.requested_cpu=definition['resources'].get('cpus');job.requested_ram_gb=definition['resources'].get('ram_gb');job.required_gpu_model=definition['resources'].get('required_gpu_model')
            item.auto_stopped=True
        item.revision+=1;s.commit();return view(s,batch)
