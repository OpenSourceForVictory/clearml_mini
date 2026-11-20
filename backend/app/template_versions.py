"""Owner-scoped copy/edit/publish; runtime approval is never inherited as authority."""
from copy import deepcopy
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_

from .core import ClientTemplate, ServerTemplate
from .clients import TemplateCreate, template_view
from .templates import ServerCreate, server_view, Clone
from .scheduling import lock
from .administration import audit


class Edit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    definition:dict


class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    action:Literal['publish','archive','delete']


class Access(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0)
    available_to_members:bool


def model_for(kind):
    if kind not in ('client','server'):raise HTTPException(404,'template kind not found')
    return ClientTemplate if kind=='client' else ServerTemplate


def view(t):return template_view(t) if isinstance(t,ClientTemplate) else server_view(t)


def visible(t,user):
    return (user.role=='admin' or t.owner_id==user.id or
            (t.owner_id is None and t.enabled and not t.archived and t.status=='published' and
             (isinstance(t,ClientTemplate) or t.available_to_members)))


def definition(t):
    schema=TemplateCreate if isinstance(t,ClientTemplate) else ServerCreate
    return schema.model_validate({k:v for k,v in view(t).items() if k in schema.model_fields}).model_dump()


def owned(s,kind,identity,user,write=False):
    t=s.get(model_for(kind),identity)
    if not t or not visible(t,user) or (write and user.role!='admin' and t.owner_id!=user.id):
        raise HTTPException(404,'template not found')
    return t


def check_revision(t,expected):
    if t.revision!=expected:raise HTTPException(409,'template changed; refresh before saving')


def unique(s,model,name,version,exclude=None):
    q=select(model.id).where(model.name==name,model.version==version)
    if exclude:q=q.where(model.id!=exclude)
    if s.scalar(q):raise HTTPException(409,'template name and version already exist')


def check_host_edit(user,old,new):
    if user.role=='admin':return
    if new['execution_mode']=='host':
        # Members may reuse approved host templates, changing only business
        # defaults/constraints. They cannot replace command, bindings or runtime.
        if old['execution_mode']!='host':raise HTTPException(403,'host execution requires administrator approval')
        for key in ('execution_mode','command','host_runtime','platform_bindings','integration_mode','result_directory'):
            if old.get(key)!=new.get(key):raise HTTPException(403,'host runtime changes require administrator approval')
        before=(old.get('parameter_schema') or {}).get('fields',{})
        after=(new.get('parameter_schema') or {}).get('fields',{})
        if set(before)!=set(after) or any(before[k].get('binding')!=after[k].get('binding') for k in before):
            raise HTTPException(403,'host parameter binding changes require administrator approval')


def install(app,db,current,admin):
    @app.get('/api/v1/runtime-templates/{kind}')
    def listing(kind:str,user=Depends(current),s=Depends(db)):
        model=model_for(kind)
        return [view(t) for t in s.scalars(select(model).where(model.archived.is_(False)).order_by(model.name,model.version)) if visible(t,user)]

    @app.post('/api/v1/runtime-templates/{kind}/{identity}/clone',status_code=201)
    def clone(kind:str,identity:str,body:Clone,user=Depends(current),s=Depends(db)):
        lock(s);source=owned(s,kind,identity,user)
        if source.archived:raise HTTPException(409,'restore source before copying')
        fields=definition(source);fields.update(name=body.name or source.name,version=body.version)
        model=model_for(kind);unique(s,model,fields['name'],fields['version'])
        if kind=='client':t=ClientTemplate(**fields,owner_id=user.id,status='draft')
        else:t=ServerTemplate(name=fields['name'],version=fields['version'],definition=fields,owner_id=user.id,status='draft')
        s.add(t);s.flush();audit(s,user,'template.copy',t.id,{'source_id':source.id});s.commit();return view(t)

    @app.put('/api/v1/runtime-templates/{kind}/{identity}')
    def edit(kind:str,identity:str,body:Edit,user=Depends(current),s=Depends(db)):
        lock(s);t=owned(s,kind,identity,user,True);check_revision(t,body.expected_revision)
        if t.status!='draft' or t.archived:raise HTTPException(409,'only active drafts can be edited; copy to a new version')
        try:fields=(TemplateCreate if kind=='client' else ServerCreate).model_validate(body.definition).model_dump()
        except ValueError as exc:
            from pydantic import ValidationError
            detail='; '.join('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False,include_context=False)) if isinstance(exc,ValidationError) else 'invalid template definition'
            raise HTTPException(400,detail) from exc
        check_host_edit(user,definition(t),fields);unique(s,model_for(kind),fields['name'],fields['version'],t.id)
        if kind=='client':
            for key,value in fields.items():setattr(t,key,value)
        else:t.name=fields['name'];t.version=fields['version'];t.definition=fields
        t.revision+=1;audit(s,user,'template.edit',t.id);s.commit();return view(t)

    @app.post('/api/v1/runtime-templates/{kind}/{identity}/actions')
    def action(kind:str,identity:str,body:Action,user=Depends(current),s=Depends(db)):
        lock(s);t=owned(s,kind,identity,user,True);check_revision(t,body.expected_revision)
        if body.action=='delete':
            if t.status!='draft':raise HTTPException(409,'only drafts can be deleted')
            s.delete(t);s.commit();return {'deleted':True}
        if body.action=='publish':
            if t.archived:raise HTTPException(409,'archived draft cannot be published')
            definition(t);t.status='published'
        else:t.archived=True;t.enabled=False
        t.revision+=1;audit(s,user,'template.'+body.action,t.id);s.commit();return view(t)

    @app.patch('/api/v1/admin/server-templates/{identity}/access')
    def access(identity:str,body:Access,user=Depends(admin),s=Depends(db)):
        lock(s);t=owned(s,'server',identity,user,True);check_revision(t,body.expected_revision)
        if t.owner_id is not None:raise HTTPException(409,'only administrator-owned templates can be published to members')
        t.available_to_members=body.available_to_members;t.revision+=1
        audit(s,user,'server-template.access',t.id);s.commit();return view(t)
