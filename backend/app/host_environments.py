"""Administrator-approved host environments and versioned readiness."""
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select
from .core import Worker,Slot,now
from .storage import StorageConfig
from .scheduling import lock,online
from .freshness import ENVIRONMENT_REPORT_SECONDS, recent
from .administration import audit,release_reservations
from runner.image_runtime import NAME


class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    python: str
    code_root: str
    @field_validator('python','code_root')
    @classmethod
    def absolute(cls,v):return StorageConfig.path(v)


class Config(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    environments:dict[str,Profile]=Field(default_factory=dict,max_length=32)
    @field_validator('environments')
    @classmethod
    def names(cls,v):
        if any(not NAME.fullmatch(k) for k in v):raise ValueError('invalid environment name')
        return v


class Resource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ready:bool
    error:str|None=Field(default=None,max_length=1000)


class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0)
    applied_revision:int=Field(ge=0)
    environments:dict[str,Resource]=Field(default_factory=dict,max_length=32)


def desired(worker):return {'revision':worker.host_revision,'environments':worker.host_environments or {}}


def blockers(worker,slot,spec,at):
    template=(spec or {}).get('template',{})
    if template.get('execution_mode','docker')!='host':return []
    if 'command_mode' in template.get('host_runtime',{}) and slot.protocol_version<14:return ['通用命令需要 Worker 协议 14，请升级节点 Worker']
    if template.get('host_runtime',{}).get('working_directory') is not None and slot.protocol_version<13:return ['业务执行目录需要 Worker 协议 13，请升级节点 Worker']
    if slot.protocol_version<7:return ['宿主机执行需要 Worker v7']
    report=slot.host_report or {}
    if report.get('revision')!=worker.host_revision or report.get('applied_revision')!=worker.host_revision or not recent(slot.host_reported_at,at,ENVIRONMENT_REPORT_SECONDS):return ['宿主机环境配置未生效或报告过期']
    name=template['host_runtime']['environment_name']
    if name not in (worker.host_environments or {}) or not report.get('environments',{}).get(name,{}).get('ready'):return ['宿主机环境未批准或不可用: '+name]
    resources=(slot.runtime_report or {}).get('assets',{})
    return ['逻辑资产未就绪: '+name for name in template['host_runtime']['assets'] if not resources.get(name,{}).get('ready')]


def acknowledge(s,worker,slot,report):
    if slot.protocol_version<7:raise HTTPException(409,'host reporting requires worker v7')
    if report.revision!=worker.host_revision:return
    if report.applied_revision>report.revision or set(report.environments)-set(worker.host_environments or {}):raise HTTPException(409,'invalid host environment report')
    slot.host_report=report.model_dump();slot.host_reported_at=now()


def view(s,w):
    return {**desired(w),'slots':[{'slot_id':x.id,'gpu_index':x.gpu_index,'online':online(x),'report':x.host_report,'reported_at':x.host_reported_at} for x in s.scalars(select(Slot).where(Slot.worker_id==w.id))]}


def install(app,db,admin):
    @app.get('/api/v1/admin/nodes/{identity}/host-environments')
    def get(identity:str,user=Depends(admin),s=Depends(db)):
        w=s.get(Worker,identity)
        if not w:raise HTTPException(404,'node not found')
        return view(s,w)
    @app.put('/api/v1/admin/nodes/{identity}/host-environments')
    def put(identity:str,body:Config,user=Depends(admin),s=Depends(db)):
        lock(s);w=s.get(Worker,identity)
        if not w or w.revoked_at:raise HTTPException(404,'node not found')
        if w.host_revision!=body.expected_revision:raise HTTPException(409,'host environment configuration changed; refresh')
        w.host_revision+=1;w.host_environments={k:v.model_dump() for k,v in body.environments.items()}
        release_reservations(s,list(s.scalars(select(Slot).where(Slot.worker_id==w.id))))
        audit(s,user,'host-environments.update',identity,{'revision':w.host_revision,'names':list(body.environments)})
        s.commit();return view(s,w)
