"""Explicit requests; business parameter names never influence allocation."""
from types import SimpleNamespace
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from fastapi import HTTPException


class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    accelerator_type:Literal['gpu','npu']|None=None
    @model_validator(mode='before')
    @classmethod
    def aliases(cls,value):
        from runner.resource_contract import request
        if isinstance(value,dict):
            # Nulls from old persisted manifests remain absent; new template
            # contracts validate explicit values during resolution.
            aliases=request({k:v for k,v in value.items() if v is not None})
            return {**{k:v for k,v in value.items() if v is None},**aliases}
        return value
    @model_serializer(mode='wrap')
    def serialize(self,handler):
        return {k:v for k,v in handler(self).items() if k in self.model_fields_set}
    cpus:int|None=Field(default=None,ge=1,le=10000)
    ram_gb:int|None=Field(default=None,ge=1,le=100000)
    gpu_mode:Literal['exclusive','shared']|None=None
    requested_vram_gb:int|None=Field(default=None,ge=0,le=10000)
    required_gpu_model:str|None=Field(default=None,min_length=1,max_length=100)


def validate(pool,request):
    shared=request.get('gpu_mode','exclusive')=='shared';vram=request.get('requested_vram_gb',0)
    if shared and (not pool.allow_shared or vram<1):raise HTTPException(400,'shared jobs require an enabled pool and positive VRAM request')
    if pool.gpu_model=='virtual' and (shared or vram):raise HTTPException(400,'virtual pools do not allocate GPU memory')
    return request


def budget(pool,job):
    return SimpleNamespace(cpu_per_job=getattr(job,'requested_cpu',None) or pool.cpu_per_job,
                           ram_gb_per_job=getattr(job,'requested_ram_gb',None) or pool.ram_gb_per_job)


def explicit(body):
    return body.model_dump(exclude_unset=True)


def legacy(body):
    return {k:getattr(body,k) for k in ('gpu_mode','requested_vram_gb','required_gpu_model') if k in body.model_fields_set}


def resolve(pool,template,*layers):
    from runner.resource_contract import resolve as resolve_contract
    contract=(template or {}).get('resource_requirements')
    # Older manifests serialized optional request defaults as null.
    if not contract:layers=[(source,{k:v for k,v in values.items() if v is not None or k=='required_gpu_model'}) for source,values in layers]
    defaults={'gpu_mode':'exclusive','requested_vram_gb':0,'required_gpu_model':None}
    if contract:defaults.update(cpus=pool.cpu_per_job,ram_gb=pool.ram_gb_per_job,accelerator_type='gpu')
    try:effective,sources=resolve_contract(contract,layers,defaults)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    return validate(pool,effective),sources


def submission(body):
    old=legacy(body);new=explicit(body.resources)
    for key in old.keys() & new.keys():
        if old[key]!=new[key]:raise HTTPException(400,'conflicting resource declarations: '+key)
    return {**old,**new}
