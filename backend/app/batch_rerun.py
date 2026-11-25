"""Read-only historical configuration -> editable submission, never execution state."""
from copy import deepcopy
from fastapi import Depends, HTTPException
from sqlalchemy import select
from .core import EvaluationBatch, EvaluationOrchestration, Job, ClientTemplate, ServerTemplate, Pool
from .clients import template_view
from .templates import server_view
from .template_versions import visible
from .job_resources import Request

RESOURCE_FIELDS = set(Request.model_fields)
VOLATILE_TEMPLATE = {'enabled', 'archived', 'status', 'revision', 'owner_id'}


def build(s, batch, jobs, user):
    issues = []
    def issue(path, code, message, fatal=False):
        issues.append(dict(path=path, code=code, message=message, fatal=fatal))
    if not jobs:
        raise HTTPException(409, '历史批次没有任务，无法导入')
    if len(jobs)>100:
        raise HTTPException(409, '历史批次超过向导支持的 100 项任务')
    specs = [j.client_spec for j in jobs]
    if any(not isinstance(x,dict) or not isinstance(x.get('template'),dict) for x in specs):
        raise HTTPException(409, '历史批次缺少客户端模板快照，无法恢复')
    orch = s.get(EvaluationOrchestration,batch.id)
    definitions = (orch.definition or {}).get('services',[]) if orch else []
    client_ids = {x['template'].get('id') for x in specs}
    server_ids = {d.get('template_id') for d in definitions}
    clients = {t.id:t for t in s.scalars(select(ClientTemplate).where(ClientTemplate.id.in_(client_ids)))}
    servers = {t.id:t for t in s.scalars(select(ServerTemplate).where(ServerTemplate.id.in_(server_ids)))}
    pool_ids = {j.pool_id for j in jobs} | {d.get('pool_id') for d in definitions}
    pools = {p.id:p for p in s.scalars(select(Pool).where(Pool.id.in_(pool_ids)))}

    def check_template(snapshot, rows, path, view):
        identity = snapshot.get('id'); row = rows.get(identity)
        if not row or not visible(row,user) or row.archived or not row.enabled or row.status!='published':
            issue(path,'template_unavailable','原模板不可用，请明确选择可用模板并核对参数')
        else:
            current = view(row)
            keys=set(current)-VOLATILE_TEMPLATE
            if any(k not in snapshot or snapshot[k]!=current[k] for k in keys):
                issue(path,'template_changed','当前模板与历史快照不同，请明确选择模板并核对配置')
        return {'id':identity,'name':snapshot.get('name'),'version':snapshot.get('version')}

    def check_pool(identity,path):
        if identity not in pools or not pools[identity].enabled:
            issue(path,'pool_unavailable','原资源池不可用，请选择当前可用资源池')

    def resources(value,path):
        if not isinstance(value,dict):
            issue(path,'resources_missing','历史资源申请未记录，无法可靠恢复',True)
            return {}
        return {k:deepcopy(v) for k,v in value.items() if k in RESOURCE_FIELDS}

    first=specs[0]
    shared_keys=('environment','request_timeout_seconds','timeout_seconds','service_mode')
    for key in shared_keys:
        if any(key not in x for x in specs):
            issue('client.'+key,'snapshot_missing','历史客户端字段未记录：'+key,True)
        elif any(x[key]!=first[key] for x in specs):
            issue('client.'+key,'heterogeneous','向导无法合并不同任务的 '+key,True)
    if any(x['template']!=first['template'] for x in specs):
        issue('client.template_id','heterogeneous','同一批次存在不同客户端模板快照，不能以首项替代',True)
    if len({j.pool_id for j in jobs})!=1:
        issue('pool_id','heterogeneous','不同任务使用不同客户端资源池，当前向导无法合并',True)
    tolerances=[x.get('disconnect_tolerance_seconds') for x in specs]
    valid_tolerances=all(value is None or (type(value) is int and 0 < value < 2**63)
                         for value in tolerances)
    if not valid_tolerances:
        issue('disconnect_tolerance_seconds','snapshot_invalid','历史断联容忍时间无效，不能自动恢复',True)
    elif any(value != tolerances[0] for value in tolerances):
        issue('disconnect_tolerance_seconds','heterogeneous','不同任务使用不同断联容忍时间，向导无法合并',True)
    original_client=check_template(first['template'],clients,'client.template_id',template_view)
    check_pool(jobs[0].pool_id,'pool_id')
    client={k:deepcopy(first[k]) for k in shared_keys if k in first}
    client.update(template_id=first['template'].get('id',''),parameters={})
    if client.get('service_mode')=='external':
        candidates=[]
        for x in specs:
            value=x.get('endpoints')
            if value is None and x.get('endpoint') and x.get('endpoint_mode'):
                value=[{'url':x['endpoint'],'mode':x['endpoint_mode']}]
            if not value:
                issue('client.endpoints','snapshot_missing','外部服务候选地址或访问模式未记录',True)
                value=[]
            candidates.append([{k:e[k] for k in ('url','mode') if k in e} for e in value])
        if any(x!=candidates[0] for x in candidates):
            issue('client.endpoints','heterogeneous','不同任务的外部服务候选地址不同，无法合并',True)
        client['endpoints']=candidates[0]
    cases=[]
    for j,x in zip(jobs,specs):
        for key in ('task_name','seed','parameters','resources'):
            if key not in x:issue('cases.'+str(j.case_id)+'.'+key,'snapshot_missing','任务快照缺少 '+key,True)
        cases.append({'case_id':j.case_id,'task_name':x.get('task_name'),'episodes':j.episodes,
                      'seed':x.get('seed'),'parameters':deepcopy(x.get('parameters',{})),
                      'resources':resources(x.get('resources'),'cases.'+str(j.case_id)+'.resources'),
                      'service_keys':deepcopy(x.get('service_keys',[]))})
    services=[];original_servers=[]
    if client.get('service_mode')=='managed':
        if not orch or not definitions:issue('services','snapshot_missing','托管服务定义快照缺失',True)
        for i,d in enumerate(definitions):
            path=f'services.{i}'
            snapshot=d.get('template')
            if not isinstance(snapshot,dict):
                issue(path,'snapshot_missing','服务模板快照缺失',True);snapshot={}
            original_servers.append(check_template(snapshot,servers,path+'.template_id',server_view))
            check_pool(d.get('pool_id'),path+'.pool_id')
            fields=('key','template_id','pool_id','slot_id','mode','parameters','environment','instance_count')
            item={k:deepcopy(d[k]) for k in fields if k in d}
            for key in ('key','template_id','pool_id','mode','parameters','environment'):
                if key not in item:issue(path+'.'+key,'snapshot_missing','服务字段缺失：'+key,True)
            if not d.get('instance_count'):
                item['instance_count']=None
                issue(path+'.instance_count','instance_count_required','旧配置只记录实例上限或未记录数量，请填写明确的实例数')
            item['resources']=resources(d.get('resources'),path+'.resources')
            services.append(item)
    return {'schema_version':1,'source':{'batch_id':batch.id,'title':batch.title,'suite_id':batch.suite_id},
            'templates':{'client':original_client,'services':original_servers},'issues':issues,
            'submission':{'title':(batch.title+'（再次运行）')[:200],'pool_id':jobs[0].pool_id,
                          'max_concurrent':batch.max_concurrent,
                          'disconnect_tolerance_seconds':tolerances[0] if valid_tolerances else None,
                          'suite_id':None,'cases':cases,
                          'resources':{},'client':client,'services':services,
                          'stop_after_batch':orch.stop_after_batch if orch else True}}


def install(app,db,current):
    @app.get('/api/v1/batches/{identity}/rerun-draft')
    def draft(identity:str,user=Depends(current),s=Depends(db)):
        batch=s.get(EvaluationBatch,identity)
        if not batch or batch.deleted_at or (batch.owner_id!=user.id and user.role!='admin'):
            raise HTTPException(404,'batch not found')
        jobs=list(s.scalars(select(Job).where(Job.batch_id==batch.id).order_by(Job.created_at,Job.id)))
        return build(s,batch,jobs,user)
