"""Logical directory mounts. No filesystem or web dependencies."""
import copy
import re

NAME=re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')


def mounts(value):
    if not isinstance(value,dict) or len(value)>16:raise ValueError('mounts must contain at most 16 logical names')
    result={}
    for name,item in value.items():
        if not isinstance(name,str) or not NAME.fullmatch(name):raise ValueError('invalid mount name')
        if not isinstance(item,dict) or set(item)-{'source','target','read_only','required'}:raise ValueError('invalid mount fields')
        source=item.get('source');target=item.get('target')
        if not isinstance(source,str) or not (source in ('work','output','cache') or re.fullmatch(r'(assets|directories):[A-Za-z][A-Za-z0-9_]{0,63}',source)):
            raise ValueError('mount source must be a platform directory or approved logical name')
        if not isinstance(target,str):raise ValueError('mount target is required')
        read_only=item.get('read_only',source not in ('work','output'));required=item.get('required',True)
        if type(read_only) is not bool or type(required) is not bool:raise ValueError('mount flags must be boolean')
        if (source=='cache' or source.startswith('assets:')) and not read_only:raise ValueError('assets and cache mounts must be read-only')
        result[name]={'source':source,'target':target,'read_only':read_only,'required':required}
    return copy.deepcopy(result)


def requirements(declarations,config,report):
    """Return only logical names, never expose machine paths to task owners."""
    problems=[]
    for name,item in declarations.items():
        source=item['source']
        if ':' not in source:continue
        kind,key=source.split(':',1)
        entry=config.get(kind,{}).get(key)
        if entry is None:
            if item['required']:problems.append('挂载映射缺失: '+name)
            continue
        if kind=='directories' and not item['read_only'] and not entry.get('writable',False):
            problems.append('挂载未批准写入: '+name)
        if not report.get(kind,{}).get(key,{}).get('ready'):
            # Optional means absent is allowed; a configured but broken resource
            # must not disappear silently from an execution.
            problems.append('挂载目录未就绪: '+name)
    return problems
