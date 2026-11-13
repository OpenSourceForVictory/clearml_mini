"""Authenticated API facade; SSH execution and temporary secrets stay outside API."""
import http.client
import json
import os
import socket
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .core import Worker
from .scheduling import lock
from .enrollment import begin, bundle
from onboarding.contracts import validate, Rejected


class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout)
        self.sock.connect(self.host)


def executor(path,body=None):
    target=os.getenv('ONBOARDING_SOCKET','')
    if not target:raise HTTPException(503,'SSH onboarding executor is not configured')
    connection=UnixHTTP(target,timeout=20)
    try:
        connection.request('POST',path,json.dumps(body or {}).encode(),{'Content-Type':'application/json'})
        response=connection.getresponse();data=response.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError()
        value=json.loads(data)
        if response.status>=400:raise HTTPException(response.status,value.get('error','onboarding executor rejected operation'))
        return value
    except (OSError,ValueError,http.client.HTTPException):raise HTTPException(503,'SSH onboarding executor is unavailable') from None
    finally:connection.close()


class Device(BaseModel):
    model_config=ConfigDict(extra='forbid')
    index:int=Field(ge=0,le=1023)
    uuid:str=Field(min_length=1,max_length=128)

class Begin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    node_id:str|None=None
    node_name:str=Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    roles:list[str]=Field(default_factory=lambda:['client'],min_length=1,max_length=2)
    expected_revision:int=Field(default=0,ge=0)
    release_id:str
    devices:list[Device]=Field(min_length=1,max_length=64)
    operation_id:str=Field(pattern=r'^[a-f0-9]{32}$')


def install(app,db,admin):
    @app.get('/api/v1/admin/ssh-onboarding')
    def listing(user=Depends(admin)):
        return executor('/list',{'actor':user.id})

    @app.post('/api/v1/admin/ssh-onboarding/check')
    def check(body:dict,request:Request,user=Depends(admin)):
        try:form=validate(body)
        except (Rejected,ValueError):raise HTTPException(400,'invalid SSH onboarding form') from None
        return executor('/check',{'actor':user.id,'form':form,'authorization':request.headers.get('authorization','')})

    @app.post('/api/v1/admin/ssh-onboarding/{identity}/confirm')
    def confirm(identity:str,user=Depends(admin)):
        return executor('/confirm',{'actor':user.id,'id':identity})

    @app.post('/api/v1/admin/ssh-onboarding/{identity}/cancel')
    def cancel(identity:str,user=Depends(admin)):
        return executor('/cancel',{'actor':user.id,'id':identity})

    @app.post('/api/v1/admin/ssh-onboarding/{identity}/trust')
    def trust(identity:str,body:dict,user=Depends(admin)):
        return executor('/trust',{'actor':user.id,'id':identity,'fingerprint':body.get('fingerprint')})

    @app.post('/api/v1/admin/ssh-enrollment/begin')
    def start(body:Begin,user=Depends(admin),s=Depends(db)):
        _,release,agent=bundle()
        if body.release_id!=release.id:raise HTTPException(409,'platform bundle changed; repeat environment check')
        if any(x not in ('client','server') for x in body.roles):raise HTTPException(400,'invalid roles')
        if len({x.index for x in body.devices})!=len(body.devices):raise HTTPException(400,'duplicate GPU index')
        lock(s)
        w=s.get(Worker,body.node_id) if body.node_id else s.scalar(select(Worker).where(Worker.node_name==body.node_name))
        if body.node_id and (not w or w.node_name!=body.node_name):raise HTTPException(404,'original machine not found')
        if not body.node_id and w:raise HTTPException(409,'node already provisioned; select original machine for re-enrollment')
        new=w is None
        if new:
            import secrets
            w=Worker(node_name=body.node_name,token_hash=secrets.token_hex(32),roles=body.roles,enabled=False)
            s.add(w);s.flush()
        w.connection={**(w.connection or {}),'ssh_operation':body.operation_id}
        return begin(s,w,user,release,agent,body.expected_revision,[x.model_dump() for x in body.devices],new=new)
