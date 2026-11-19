"""Bounded request/response mailbox; never wait for a node with a DB connection."""
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import select,delete,func
from sqlalchemy.exc import IntegrityError
from .core import ExecutionLogCatalog as Catalog,LogReadRequest as Read,LogReadSubscriber as Subscriber,Job,Run,Slot,User,Worker,now
from .execution_locations import visible_run
from .scheduling import TERMINAL,utc
from runner.process_log_contract import identity,absolute_directory,relative_directory,PAGE_BYTES


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


class Registration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:Literal['task','business','episode','service']
    episode_id:int=Field(default=0,ge=0,le=10000,strict=True)
    generation:str
    root_path:str
    status:Literal['open','closed','incomplete','missing','cleaned','failed']='open'
    first_sequence:int=Field(default=0,ge=0,le=2**63-1,strict=True)
    next_sequence:int=Field(default=0,ge=0,le=2**63-1,strict=True)
    dropped_bytes:int=Field(default=0,ge=0,le=2**63-1,strict=True)
    truncated:bool=False
    policy_revision:int=Field(default=0,ge=0,strict=True)

    @model_validator(mode='after')
    def valid(self):
        identity('00000000-0000-0000-0000-000000000000',self.scope,self.episode_id)
        absolute_directory(self.root_path)
        if str(UUID(self.generation))!=self.generation or self.first_sequence>self.next_sequence:raise ValueError('invalid log range')
        return self


class Query(BaseModel):
    model_config=ConfigDict(extra='forbid')
    log_id:str
    cursor:str|None=Field(default=None,max_length=512)
    direction:Literal['forward','backward','tail']='tail'
    stream:Literal['all','stdout','stderr','system']='all'
    limit_bytes:int=Field(default=PAGE_BYTES,ge=16384,le=PAGE_BYTES,strict=True)
    until:int|None=Field(default=None,ge=0,le=2**63-1,strict=True)


class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid')
    slot_id:str|None=Field(default=None,min_length=1,max_length=64)


class Reply(BaseModel):
    model_config=ConfigDict(extra='forbid')
    claim:str=Field(min_length=16,max_length=128)
    generation:str
    status:Literal['ready','missing','cleaned','failed','busy','superseded']
    page:dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.page,ensure_ascii=True,allow_nan=False).encode())>PAGE_BYTES:raise ValueError('log page too large')
        return self


def purge(s):
    expired=list(s.scalars(select(Read.id).where(Read.expires_at<=now()).with_for_update(skip_locked=True)))
    if expired:
        s.execute(delete(Subscriber).where(Subscriber.request_id.in_(expired)))
        s.execute(delete(Read).where(Read.id.in_(expired)))


def catalog_view(row):
    return {**{k:getattr(row,k) for k in ['id','run_id','execution_id','scope','episode_id','generation','status','first_sequence','next_sequence','truncated','dropped_bytes','updated_at','policy_revision']},
            'path':row.root_path+'/'+relative_directory(row.scope,row.episode_id),'source':'node_catalog'}


def install(app,db,current,worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/log-catalog')
    def register(execution_id:str,body:Registration,worker=Depends(worker_auth),s=Depends(db)):
        run=s.scalar(select(Run).where(Run.execution_id==execution_id).with_for_update())
        slot=s.get(Slot,run.slot_id) if run else None
        if not slot or slot.worker_id!=worker.id:raise HTTPException(404,'execution not found')
        row=s.scalar(select(Catalog).where(Catalog.execution_id==execution_id,Catalog.scope==body.scope,Catalog.episode_id==body.episode_id))
        if body.episode_id>s.get(Job,run.job_id).episodes:raise HTTPException(400,'episode outside task')
        if row:
            if row.worker_id!=worker.id or row.slot_id!=slot.id or row.generation!=body.generation or row.root_path!=body.root_path:
                raise HTTPException(409,'log identity cannot be rebound')
            if row.status in ['missing','cleaned'] and body.status!=row.status:
                raise HTTPException(409,'log data is no longer available')
            if row.status!='open' and body.status=='open':raise HTTPException(409,'log cannot reopen')
            if body.next_sequence<row.next_sequence or body.first_sequence<row.first_sequence:raise HTTPException(409,'stale log range')
        else:
            if run.status not in ['STARTING','RUNNING','CANCEL_REQUESTED'] or not run.lease_expires_at or utc(run.lease_expires_at)<=now():
                anchor=s.scalar(select(Catalog).where(Catalog.execution_id==execution_id,Catalog.scope=='task',Catalog.generation==body.generation,Catalog.root_path==body.root_path))
                if not anchor or anchor.status in ['missing','cleaned'] or body.status=='open':raise HTTPException(409,'new log requires live execution')
            row=Catalog(run_id=run.id,execution_id=execution_id,worker_id=worker.id,slot_id=slot.id,**body.model_dump())
            s.add(row)
        for k,v in body.model_dump().items():setattr(row,k,v)
        row.updated_at=now();s.commit();return catalog_view(row)

    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/log-catalog')
    def listing(job_id:str,run_id:str,user=Depends(current),s=Depends(db)):
        run=visible_run(s,job_id,run_id,user)
        from .placement import view as placement_view
        return {'execution_id':run.execution_id,'placement':placement_view(s.get(Slot,run.slot_id) if run.slot_id else None),'items':[catalog_view(r) for r in s.scalars(select(Catalog).where(Catalog.run_id==run.id).order_by(Catalog.scope,Catalog.episode_id))]}

    @app.post('/api/v1/jobs/{job_id}/attempts/{run_id}/log-reads',status_code=202)
    def request(job_id:str,run_id:str,body:Query,user=Depends(current),s=Depends(db)):
        run=visible_run(s,job_id,run_id,user)
        row=s.get(Catalog,body.log_id)
        if not row or row.run_id!=run.id:raise HTTPException(404,'log not found')
        # Serialize only this user's admission, never the scheduler or execution.
        s.scalar(select(User.id).where(User.id==user.id).with_for_update())
        purge(s)
        query=body.model_dump();fingerprint=digest(json.dumps([row.id,row.generation,query],sort_keys=True))
        item=s.scalar(select(Read).where(Read.fingerprint==fingerprint,Read.expires_at>now()).order_by(Read.expires_at.desc()).limit(1))
        subscriber=s.get(Subscriber,(item.id,user.id)) if item else None
        if not subscriber:
            count=s.scalar(select(func.count()).select_from(Subscriber).join(Read).where(Subscriber.user_id==user.id,Read.status.in_(['queued','claimed'])))
            if count>=8:raise HTTPException(429,'too many pending log reads',headers={'Retry-After':'2'})
        if item is None:
            used=set(s.scalars(select(Read.budget_slot)))
            for index in range(128):
                if index in used:continue
                try:
                    with s.begin_nested():
                        item=Read(log_id=row.id,run_id=run.id,worker_id=row.worker_id,budget_slot=index,
                            fingerprint=fingerprint,generation=row.generation,query=query,status='queued',expires_at=now()+timedelta(seconds=30))
                        s.add(item);s.flush()
                    break
                except IntegrityError:item=None
            if item is None:raise HTTPException(429,'log read capacity reached',headers={'Retry-After':'2'})
        if subscriber is None:
            subscriber=Subscriber(request_id=item.id,user_id=user.id);s.add(subscriber)
        s.commit();return {'read_id':subscriber.access_id,'status':item.status,'expires_at':item.expires_at}

    @app.get('/api/v1/log-reads/{access_id}')
    def result(access_id:str,user=Depends(current),s=Depends(db)):
        sub=s.scalar(select(Subscriber).where(Subscriber.access_id==access_id,Subscriber.user_id==user.id))
        item=s.get(Read,sub.request_id) if sub else None
        if not item:raise HTTPException(404,'log read not found')
        run=s.get(Run,item.run_id)
        if not run:raise HTTPException(404,'execution not found')
        visible_run(s,run.job_id,run.id,user)
        if utc(item.expires_at)<=now():return {'status':'expired'}
        return {'status':item.status,'generation':item.generation,'page':item.response,'source':'platform_cache' if item.response is not None else None}

    @app.post('/api/v1/worker/log-reads/claim')
    def claim(body:Claim|None=None,worker=Depends(worker_auth),s=Depends(db)):
        slot_id=body.slot_id if body else None
        if slot_id and not s.scalar(select(Slot.id).where(Slot.id==slot_id,Slot.worker_id==worker.id)):raise HTTPException(404,'slot not found')
        s.scalar(select(Worker.id).where(Worker.id==worker.id).with_for_update())
        purge(s)
        active=s.scalar(select(func.count()).select_from(Read).where(Read.worker_id==worker.id,Read.status=='claimed'))
        rows=s.scalars(select(Read).where(Read.worker_id==worker.id,Read.status=='queued',*( [Read.log_id.in_(select(Catalog.id).where(Catalog.slot_id==slot_id))] if slot_id else [])).order_by(Read.expires_at).limit(max(0,2-active)).with_for_update(skip_locked=True)).all()
        out=[]
        for row in rows:
            catalog=s.get(Catalog,row.log_id);secret=secrets.token_urlsafe(24)
            row.claim_hash=digest(secret);row.status='claimed'
            out.append({'id':row.id,'claim':secret,'generation':row.generation,'execution_id':catalog.execution_id,
                        'scope':catalog.scope,'episode_id':catalog.episode_id,'query':row.query})
        s.commit();return {'requests':out}

    @app.post('/api/v1/worker/log-reads/{read_id}/reply')
    def reply(read_id:str,body:Reply,worker=Depends(worker_auth),s=Depends(db)):
        row=s.scalar(select(Read).where(Read.id==read_id,Read.worker_id==worker.id).with_for_update())
        if not row:raise HTTPException(404,'log read not found')
        if utc(row.expires_at)<=now():raise HTTPException(409,'log read expired')
        if not row.claim_hash or not secrets.compare_digest(row.claim_hash,digest(body.claim)) or body.generation!=row.generation:
            raise HTTPException(409,'log read claim mismatch')
        if row.status!='claimed':
            if row.status==body.status and row.response==body.page:return {'accepted':True}
            raise HTTPException(409,'log read already completed')
        row.status=body.status;row.response=body.page;s.commit();return {'accepted':True}
