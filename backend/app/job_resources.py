"""Explicit requests; business parameter names never influence allocation."""
from types import SimpleNamespace
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from fastapi import HTTPException


class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cpus:int|None=Field(default=None,ge=1,le=10000)
    ram_gb:int|None=Field(default=None,ge=1,le=100000)
    gpu_mode:Literal['exclusive','shared']|None=None
    requested_vram_gb:int|None=Field(default=None,ge=0,le=10000)
    required_gpu_model:str|None=Field(default=None,min_length=1,max_length=100)


def validate(pool,request):
    if request.get('cpus',pool.cpu_per_job)>pool.cpu_per_job or request.get('ram_gb',pool.ram_gb_per_job)>pool.ram_gb_per_job:
        raise HTTPException(400,'CPU/RAM request exceeds resource pool per-task budget')
    shared=request.get('gpu_mode','exclusive')=='shared';vram=request.get('requested_vram_gb',0)
    if shared and (not pool.allow_shared or vram<1):raise HTTPException(400,'shared jobs require an enabled pool and positive VRAM request')
    if pool.gpu_model=='virtual' and (shared or vram):raise HTTPException(400,'virtual pools do not allocate GPU memory')
    return request


def budget(pool,job):
    return SimpleNamespace(cpu_per_job=getattr(job,'requested_cpu',None) or pool.cpu_per_job,
                           ram_gb_per_job=getattr(job,'requested_ram_gb',None) or pool.ram_gb_per_job)
