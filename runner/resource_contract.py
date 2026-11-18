"""Versioned template requests. Pure validation; no scheduler or database imports."""
from copy import deepcopy

ALIASES={'accelerator_mode':'gpu_mode','accelerator_memory_gb':'requested_vram_gb','accelerator_model':'required_gpu_model'}
FIELDS={'cpus','ram_gb','accelerator_type','gpu_mode','requested_vram_gb','required_gpu_model','cpu_unlimited','memory_unlimited'}
LIMITS={'cpus':(1,10000),'ram_gb':(1,100000),'requested_vram_gb':(0,10000)}


def request(value):
    if not isinstance(value,dict):raise ValueError('resources must be an object')
    result={}
    for key,v in value.items():
        name=ALIASES.get(key,key)
        if name not in FIELDS:raise ValueError('unknown resource: '+key)
        if name in result:raise ValueError('duplicate resource aliases: '+name)
        if name in LIMITS:
            lo,hi=LIMITS[name]
            if type(v) is not int or not lo<=v<=hi:raise ValueError(name+': invalid resource value')
        elif name in ('cpu_unlimited','memory_unlimited'):
            if type(v) is not bool:raise ValueError(name+': expected boolean')
        elif name=='accelerator_type':
            if v not in ('gpu','npu'):raise ValueError('accelerator_type must be gpu or npu')
        elif name=='gpu_mode':
            if v not in ('shared','exclusive'):raise ValueError('gpu_mode must be shared or exclusive')
        elif v is not None and (not isinstance(v,str) or not v.strip() or len(v)>100):raise ValueError('required_gpu_model: invalid model')
        result[name]=v
    return result


def validate(value):
    if value is None:return None
    if not isinstance(value,dict) or set(value)-{'version','cpu','memory','accelerator','allow_overrides'}:raise ValueError('invalid resource_requirements')
    if type(value.get('version')) is not int or value['version']!=1:raise ValueError('resource_requirements version must be 1')
    result=deepcopy(value)
    for key,field in [('cpu','cpus'),('memory','ram_gb')]:
        part=result.get(key)
        if not isinstance(part,dict) or set(part)!={'minimum','default'}:raise ValueError(key+' requires minimum and default')
        request({field:part['minimum']});request({field:part['default']})
        if part['default']<part['minimum']:raise ValueError(key+' default is below minimum')
    acc=result.get('accelerator')
    if not isinstance(acc,dict) or set(acc)-{'type','mode','model','memory'} or not {'type','mode','memory'}<=set(acc):raise ValueError('invalid accelerator requirements')
    memory=acc['memory']
    if not isinstance(memory,dict) or set(memory)!={'minimum','default'}:raise ValueError('accelerator memory requires minimum and default')
    request({'accelerator_type':acc['type'],'gpu_mode':acc['mode'],'required_gpu_model':acc.get('model'),'requested_vram_gb':memory['minimum']})
    request({'requested_vram_gb':memory['default']})
    if memory['default']<memory['minimum']:raise ValueError('accelerator memory default is below minimum')
    if acc['mode']=='shared' and memory['default']<1:raise ValueError('shared accelerator requires positive memory')
    overrides=result.setdefault('allow_overrides',[])
    if not isinstance(overrides,list) or any(not isinstance(k,str) or k not in FIELDS-{'cpu_unlimited','memory_unlimited'} for k in overrides) or len(set(overrides))!=len(overrides):raise ValueError('invalid resource allow_overrides')
    return result


def resolve(contract,layers,legacy_defaults):
    contract=validate(contract)
    effective=dict(legacy_defaults);sources={key:'pool' for key in effective}
    if contract:
        a=contract['accelerator']
        effective.update(cpus=contract['cpu']['default'],ram_gb=contract['memory']['default'],accelerator_type=a['type'],gpu_mode=a['mode'],requested_vram_gb=a['memory']['default'],required_gpu_model=a.get('model'))
        sources={key:'template' for key in effective}
    for source,values in layers:
        values=request(values)
        for key,value in values.items():
            permission={'cpu_unlimited':'cpus','memory_unlimited':'ram_gb'}.get(key,key)
            if contract and permission not in contract['allow_overrides'] and value!=effective.get(key,False if key.endswith('_unlimited') else None):raise ValueError(key+': resource override is locked by template')
            # Validate each layer, so invalid submission cannot be hidden by a case.
            if contract:
                minimum={'cpus':contract['cpu']['minimum'],'ram_gb':contract['memory']['minimum'],'requested_vram_gb':contract['accelerator']['memory']['minimum']}.get(key)
                if minimum is not None and value<minimum:raise ValueError(key+': resource request below template minimum')
            effective[key]=value;sources[key]=source
    for flag,axis,part in [('cpu_unlimited','cpus','cpu'),('memory_unlimited','ram_gb','memory')]:
        if effective.get(flag):
            if contract and not any(axis in values for _,values in layers):
                effective[axis]=contract[part]['minimum'];sources[axis]='template_minimum'
            if (not contract and not any(axis in values for _,values in layers)) or type(effective.get(axis)) is not int or effective[axis]<1:
                raise ValueError(axis+': unlimited runtime requires a positive scheduling minimum')
    if effective.get('gpu_mode')=='shared' and effective.get('requested_vram_gb',0)<1:raise ValueError('shared accelerator requires positive memory')
    return effective,sources
