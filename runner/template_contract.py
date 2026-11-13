"""Shared validation for Docker and trusted host runtime definitions."""
import re
from .image_runtime import environment, NAME, placeholders


def host_config(value):
    if not isinstance(value, dict) or set(value) - {'environment_name','environment','overridable_env','assets'}:
        raise ValueError('invalid host runtime fields')
    name=value.get('environment_name')
    if not isinstance(name,str) or not NAME.fullmatch(name): raise ValueError('host environment_name is required')
    env=environment(value.get('environment',{}));overrides=value.get('overridable_env',[]);assets=value.get('assets',[])
    if not isinstance(overrides,list) or len(overrides)>64 or any(not isinstance(k,str) for k in overrides): raise ValueError('invalid overridable_env')
    environment(dict.fromkeys(overrides,''))
    if not isinstance(assets,list) or len(assets)>16 or any(not isinstance(k,str) or not NAME.fullmatch(k) for k in assets): raise ValueError('invalid host logical assets')
    for text in env.values(): placeholders(text,assets)
    return {'environment_name':name,'environment':env,'overridable_env':list(dict.fromkeys(overrides)),'assets':list(dict.fromkeys(assets))}


def command(value):
    if not isinstance(value,list) or not 1<=len(value)<=64 or any(not isinstance(x,str) or not x or len(x)>2000 or '\x00' in x for x in value):
        raise ValueError('command must be a bounded argv list')
    return value
