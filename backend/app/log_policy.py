"""Node intent, per-card receipts and immutable per-attempt log budgets."""
from copy import deepcopy
from typing import Literal
from fastapi import Depends, HTTPException, Body
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from .core import Worker, Slot, ExecutionLogPolicy, now
from runner.process_log_contract import DEFAULT_POLICY, validate_policy

KEY='process_log_policy'
CAPABILITY='process_log_policy_v1'


def desired(worker):
    return deepcopy((worker.connection or {}).get(KEY) or {'revision':0,'policy':DEFAULT_POLICY})


def effective(slot):
    report=(slot.software or {}).get(KEY) or {}
    return {'revision':report.get('applied_revision',0),'policy':deepcopy(report.get('policy') or DEFAULT_POLICY)}


class PolicyUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0,strict=True)
    policy:dict
    @field_validator('policy')
    @classmethod
    def valid(cls,value):return validate_policy(value)


class Receipt(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=0,strict=True)
    status:Literal['applied','failed']
    policy:dict|None=None
    error:str|None=Field(default=None,max_length=300)
    @field_validator('policy')
    @classmethod
    def valid(cls,value):return validate_policy(value) if value is not None else value


def snapshot(s,run,slot):
    if CAPABILITY not in (slot.software or {}).get('capabilities',[]):return None
    item=s.get(ExecutionLogPolicy,run.id)
    if item is None:
        item=ExecutionLogPolicy(run_id=run.id,snapshot=effective(slot))
        s.add(item);s.flush()
    return deepcopy(item.snapshot)


def merge_software(previous, incoming):
    receipt=(previous or {}).get(KEY)
    if incoming is not None and receipt is not None:
        incoming={**incoming,KEY:receipt}
    return incoming


def install(app,db,admin,worker_auth):
    @app.get('/api/v1/admin/nodes/{identity}/log-policy')
    def read(identity:str,user=Depends(admin),s=Depends(db)):
        worker=s.get(Worker,identity)
        if not worker:raise HTTPException(404,'node not found')
        return {**desired(worker),'slots':[{'id':slot.id,'device_index':slot.gpu_index,
            'accelerator_type':slot.accelerator_type,'supported':CAPABILITY in (slot.software or {}).get('capabilities',[]),
            'report':(slot.software or {}).get(KEY),'effective':effective(slot)}
            for slot in s.scalars(select(Slot).where(Slot.worker_id==identity).order_by(Slot.gpu_index))]}

    @app.put('/api/v1/admin/nodes/{identity}/log-policy')
    def update(identity:str,body:PolicyUpdate,user=Depends(admin),s=Depends(db)):
        worker=s.scalar(select(Worker).where(Worker.id==identity).with_for_update())
        if not worker:raise HTTPException(404,'node not found')
        old=desired(worker)
        if old['revision']!=body.expected_revision:raise HTTPException(409,'log policy changed; refresh')
        value={'revision':old['revision']+1,'policy':body.policy}
        worker.connection={**(worker.connection or {}),KEY:value}
        from .administration import audit
        audit(s,user,'log-policy.update',identity)
        s.commit();return value

    @app.post('/api/v1/worker/slots/{identity}/log-policy')
    def sync(identity:str,body:dict=Body(default={}),worker=Depends(worker_auth),s=Depends(db)):
        slot=s.scalar(select(Slot).where(Slot.id==identity,Slot.worker_id==worker.id).with_for_update())
        if not slot:raise HTTPException(404,'slot not found')
        value=desired(worker)
        if body:
            from pydantic import ValidationError
            try:body=Receipt.model_validate(body)
            except ValidationError:raise HTTPException(422,'invalid log policy receipt')
            if CAPABILITY not in (slot.software or {}).get('capabilities',[]):raise HTTPException(409,'log policy requires Worker upgrade')
            if body.revision!=value['revision']:raise HTTPException(409,'log policy changed; refresh')
            previous=effective(slot)
            if body.status=='applied':
                if body.policy!=value['policy']:raise HTTPException(409,'log policy acknowledgement mismatch')
                previous={'revision':body.revision,'policy':body.policy}
            slot.software={**(slot.software or {}),KEY:{'revision':body.revision,'status':body.status,
                'applied_revision':previous['revision'],'policy':previous['policy'],
                'error':body.error if body.status=='failed' else None,'reported_at':now().isoformat()}}
            s.commit()
        return value
