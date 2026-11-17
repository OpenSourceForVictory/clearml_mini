"""Shared validation for Docker and trusted host runtime definitions."""
import re
from .image_runtime import environment, NAME, placeholders


def host_config(value):
    if not isinstance(value, dict) or set(value) - {'environment_name','environment','overridable_env','assets','working_directory','command_mode','environment_source','episode_launch'}:
        raise ValueError('invalid host runtime fields')
    mode=value.get('command_mode','legacy')
    if mode not in ('legacy','exec'):raise ValueError('invalid command_mode')
    source=value.get('environment_source','explicit')
    if source not in ('explicit','login'):raise ValueError('invalid environment_source')
    episode_launch=value.get('episode_launch','whole_task')
    if episode_launch not in ('whole_task','per_episode'):raise ValueError('invalid episode_launch')
    name=value.get('environment_name')
    if not isinstance(name,str) or not NAME.fullmatch(name): raise ValueError('host environment_name is required')
    env=environment(value.get('environment',{}));overrides=value.get('overridable_env',[]);assets=value.get('assets',[])
    if not isinstance(overrides,list) or len(overrides)>64 or any(not isinstance(k,str) for k in overrides): raise ValueError('invalid overridable_env')
    environment(dict.fromkeys(overrides,''))
    if not isinstance(assets,list) or len(assets)>16 or any(not isinstance(k,str) or not NAME.fullmatch(k) for k in assets): raise ValueError('invalid host logical assets')
    for text in env.values(): placeholders(text,assets)
    directory=value.get('working_directory')
    if directory is not None:
        from pathlib import PurePosixPath
        if not isinstance(directory,str) or not directory.strip() or len(directory)>2000 or any(c in directory for c in ('\x00','\n','\r')) or '..' in PurePosixPath(directory).parts or '${' in directory:
            raise ValueError('invalid host working_directory')
    return {**({'command_mode':mode} if 'command_mode' in value else {}),**({'environment_source':source} if 'environment_source' in value else {}),**({'episode_launch':episode_launch} if 'episode_launch' in value else {}),**({'working_directory':directory} if directory is not None else {}),'environment_name':name,'environment':env,'overridable_env':list(dict.fromkeys(overrides)),'assets':list(dict.fromkeys(assets))}


def command(value):
    if not isinstance(value,list) or not 1<=len(value)<=64 or any(not isinstance(x,str) or not x or len(x)>2000 or '\x00' in x for x in value):
        raise ValueError('command must be a bounded argv list')
    return value
