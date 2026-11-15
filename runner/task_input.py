"""Versioned, stdlib-only task input and explicit platform argument bindings.

No network, scheduler credentials, shell interpolation or resource decisions.
Legacy CS fields are retained when the Worker adds the normalized envelope.
"""
from copy import deepcopy
import argparse
import json
import os
from pathlib import Path

from .parameter_contract import bind_values, validate_schema, NAME

VERSION = 1
SOURCES = {
    'platform.job_id':'string', 'platform.execution_id':'string',
    'platform.service.endpoint':'string', 'platform.service.key':'string',
    'platform.listen.address':'string', 'platform.listen.port':'integer',
    'platform.paths.work':'string', 'platform.paths.output':'string',
    'platform.paths.cache':'string', 'task.case_id':'string',
    'task.task_name':'string', 'task.episodes':'integer', 'task.seed':'integer',
}


def binding_schema(bindings):
    if not isinstance(bindings,list) or len(bindings)>32:
        raise ValueError('platform_bindings must contain at most 32 mappings')
    fields={}
    for i,b in enumerate(bindings):
        if not isinstance(b,dict) or set(b)-{'source','target','name','encoding'}:
            raise ValueError('invalid platform binding')
        source=b.get('source')
        kind=SOURCES.get(source) if isinstance(source,str) else None
        if kind is None and isinstance(source,str) and source.startswith('platform.paths.assets.') and NAME.fullmatch(source.removeprefix('platform.paths.assets.')):
            kind='string'
        if kind is None: raise ValueError('unknown platform binding source')
        fields['field'+str(i)]={'type':kind,'required':True,'binding':{k:v for k,v in b.items() if k!='source'}}
    return validate_schema({'version':1,'fields':fields})


def validate_combined(business_schema, bindings):
    platform=binding_schema(bindings)
    fields=deepcopy(business_schema['fields']) if business_schema else {}
    for i,field in enumerate(platform['fields'].values()):
        key='platformBinding'+str(i)
        while key in fields: key+='X'
        fields[key]=field
    validate_schema({'version':1,'fields':fields})


def bind_platform(bindings, document, command, environment=None):
    schema=binding_schema(bindings);values={}
    for i,b in enumerate(bindings):
        value=document
        try:
            for part in b['source'].split('.'): value=value[part]
        except (KeyError,TypeError):
            raise ValueError('platform binding source is unavailable: '+b['source']) from None
        values['field'+str(i)]=value
    return bind_values(schema,values,command,environment)


def build(config, environment=None):
    """Called after endpoint/device allocation and runtime path resolution."""
    env=os.environ if environment is None else environment
    runtime=config.get('template',{}).get('runtime') or {}
    assets={k.removeprefix('assets:'):v for k,v in config.get('_resolved_assets',{}).items() if k.startswith('assets:')}
    if not assets: assets=deepcopy(runtime.get('assets',{}))
    paths={'work':env.get('MINI_EVAL_WORK_DIR',runtime.get('work_dir','/tmp/mini-eval-work')),
           'output':env.get('MINI_EVAL_OUTPUT_DIR',runtime.get('output_dir','/tmp/mini-eval-output')),
           'cache':env.get('MINI_EVAL_CACHE_DIR','/opt/mini-eval/cache'),'assets':assets}
    platform={'job_id':config.get('job_id',''),'execution_id':config.get('execution_id',''),
              'paths':paths,'service':None,'listen':None}
    if config.get('endpoint') and config.get('workload')!='service':
        platform['service']={'key':config.get('service_key','default'),'endpoint':config['endpoint']}
    if config.get('workload')=='service':
        platform['listen']={'address':config['bind_host'],'port':config['port']}
    task={k:deepcopy(config[k]) for k in ('case_id','task_name','episodes','seed') if k in config}
    result=deepcopy(config)
    result.update(input_version=VERSION,platform=platform,task=task,business=deepcopy(config.get('parameters',{})))
    return result


def read(path=None):
    path=path or os.environ.get('MINI_EVAL_INPUT')
    if not path: raise ValueError('MINI_EVAL_INPUT is not set')
    with Path(path).open('rb') as stream: raw=stream.read(1048577)
    if len(raw)>1048576: raise ValueError('task input exceeds 1 MiB')
    def invalid_constant(_): raise ValueError('task input must contain finite JSON values')
    config=json.loads(raw,parse_constant=invalid_constant)
    if not isinstance(config,dict): raise ValueError('task input must be an object')
    if 'input_version' not in config: return build(config)
    if type(config['input_version']) is not int or config['input_version']!=VERSION:
        raise ValueError('unsupported task input version')
    if any(not isinstance(config.get(key),dict) for key in ('platform','task','business')):
        raise ValueError('task input requires platform, task and business objects')
    return config


def main():
    parser=argparse.ArgumentParser(description='Validate task input and print argv/environment without executing a program.')
    parser.add_argument('input');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();config=read(args.input);template=config.get('template',{})
    bindings=template.get('platform_bindings',[]);schema=template.get('parameter_schema')
    validate_combined(schema,bindings)
    command=template.get('command',['program']);env={}
    if schema: command,env=bind_values(schema,config['business'],command)
    command,env=bind_platform(bindings,config,command,env)
    print(json.dumps({'valid':True,**({'argv':command,'environment':env} if args.dry_run else {})},ensure_ascii=False))


if __name__=='__main__':main()
