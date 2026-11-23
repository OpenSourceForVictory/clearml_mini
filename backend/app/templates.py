"""Server template registry; execution instances always retain a snapshot."""
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator
from sqlalchemy import select
from .clients import TemplateCreate,TemplateState,template_view
from .core import ClientTemplate,ServerTemplate
from .administration import audit
from .scheduling import lock


class ServerCreate(TemplateCreate):
    result_directory: None = Field(default=None,exclude=True)
    port: int = Field(default=8000,ge=1024,le=65535)
    protocol: Literal['http','https','ws','wss']='http'
    health_path: str = Field(default='/healthz',min_length=1,max_length=500,pattern=r'^/[^\s]*$')
    health_type: Literal['http','tcp']='http'
    startup_timeout: int=Field(default=120,ge=5,le=1800)

    readiness: dict | None = None

    @field_validator('readiness')
    @classmethod
    def readiness_config(cls, value):
        from runner.readiness_contract import validate
        return validate(value) if value is not None else None

    @model_validator(mode='after')
    def service_only(self):
        if self.readiness and self.readiness['port']==self.port:raise ValueError('independent health port must differ from business port; use business to share')
        if self.task_type!='evaluation':raise ValueError('server templates use service lifecycle, not command task completion')
        if self.integration_mode != 'direct': raise ValueError('service templates do not use evaluation SDK loops')
        if self.execution_mode=='host' and self.host_runtime.get('episode_launch')=='per_episode':
            raise ValueError('server templates cannot launch per episode')
        if self.execution_mode == 'docker' and self.runtime is None:
            from runner.image_runtime import validate
            self.runtime = validate({}, self.command)
        return self


class Clone(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str|None=Field(default=None,min_length=1,max_length=100)
    version: str=Field(min_length=1,max_length=100)


def server_view(t):
    return {'description':'',**t.definition,'id':t.id,'name':t.name,'version':t.version,'enabled':t.enabled,'archived':t.archived,
            'owner_id':t.owner_id,'status':t.status,'revision':t.revision,'available_to_members':t.available_to_members}


def install(app,db,admin):
    @app.get('/api/v1/admin/server-templates')
    def listing(user=Depends(admin),s=Depends(db)):
        return [server_view(t) for t in s.scalars(select(ServerTemplate).where(ServerTemplate.archived.is_(False)).order_by(ServerTemplate.name,ServerTemplate.version))]

    @app.post('/api/v1/admin/server-templates',status_code=201)
    def create(body:ServerCreate,user=Depends(admin),s=Depends(db)):
        lock(s)
        if s.scalar(select(ServerTemplate.id).where(ServerTemplate.name==body.name,ServerTemplate.version==body.version)):raise HTTPException(409,'server template version already exists')
        t=ServerTemplate(name=body.name,version=body.version,definition=body.model_dump());s.add(t);s.flush();audit(s,user,'server-template.create',t.id);s.commit();return server_view(t)

    @app.patch('/api/v1/admin/server-templates/{identity}')
    def state(identity:str,body:TemplateState,user=Depends(admin),s=Depends(db)):
        lock(s);t=s.get(ServerTemplate,identity)
        if not t or t.archived:raise HTTPException(404,'template not found')
        t.enabled=body.enabled;s.commit();return server_view(t)

    @app.delete('/api/v1/admin/{kind}-templates/{identity}')
    def archive(kind:str,identity:str,user=Depends(admin),s=Depends(db)):
        lock(s)
        model=ClientTemplate if kind=='client' else ServerTemplate if kind=='server' else None
        if model is None:raise HTTPException(404,'template type not found')
        t=s.get(model,identity)
        if not t:raise HTTPException(404,'template not found')
        t.enabled=False;t.archived=True;audit(s,user,kind+'-template.archive',identity);s.commit();return {'archived':True}

    @app.post('/api/v1/admin/{kind}-templates/{identity}/clone',status_code=201)
    def clone(kind:str,identity:str,body:Clone,user=Depends(admin),s=Depends(db)):
        lock(s)
        model=ClientTemplate if kind=='client' else ServerTemplate if kind=='server' else None
        if model is None:raise HTTPException(404,'template type not found')
        t=s.get(model,identity)
        if not t:raise HTTPException(404,'template not found')
        name=body.name or t.name
        if s.scalar(select(model.id).where(model.name==name,model.version==body.version)):raise HTTPException(409,'template version already exists')
        if kind=='client':
            fields={k:v for k,v in template_view(t).items() if k in TemplateCreate.model_fields};fields.update(name=name,version=body.version)
            new=ClientTemplate(**TemplateCreate(**fields).model_dump(),owner_id=t.owner_id)
        else:
            fields=ServerCreate(**{**t.definition,'name':name,'version':body.version}).model_dump();new=ServerTemplate(name=name,version=body.version,definition=fields)
        s.add(new);s.flush();audit(s,user,kind+'-template.clone',new.id,{'source_id':identity});s.commit();return template_view(new) if kind=='client' else server_view(new)
