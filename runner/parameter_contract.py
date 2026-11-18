"""Versioned business parameter validation and shell-free process bindings.

Pure stdlib contract shared by API and runners. No network, database or execution.
Callers retain ownership of task identity, scheduling and platform environment.
"""
from copy import deepcopy
import json
import math
import re

from .image_runtime import environment

VERSION = 1
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
BUSINESS_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
TYPES = {'string', 'integer', 'number', 'boolean', 'enum', 'object', 'array'}
RESERVED = {'task_name', 'episodes', 'seed', 'endpoint', 'execution_id', 'input_path',
            'work_dir', 'output_dir', 'episode_id', 'episode_seed', 'result_path', 'port', 'health_port', 'bind_host',
            'platform', 'task', 'business', 'resources', 'job_id', 'input_version'}


def encoded(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('parameters must contain finite JSON values') from exc
    if len(result.encode()) > 16384:
        raise ValueError('parameters exceed 16 KiB')
    return result


def fail(path, message):
    # Report the field, never its potentially sensitive value.
    raise ValueError(f'{path}: {message}')


def check(value, spec, path):
    kind = spec['type']
    valid = {
        'string': lambda: isinstance(value, str) and '\x00' not in value,
        'integer': lambda: type(value) is int,
        'number': lambda: type(value) in (int, float) and (type(value) is int or math.isfinite(value)),
        'boolean': lambda: type(value) is bool,
        'enum': lambda: any(type(value) is type(x) and value == x for x in spec['enum']),
        'object': lambda: isinstance(value, dict) and all(isinstance(k, str) for k in value),
        'array': lambda: isinstance(value, list),
    }[kind]()
    if not valid:
        fail(path, 'expected '+kind)
    if kind in ('number', 'integer'):
        if 'min' in spec and value < spec['min']: fail(path, 'below minimum')
        if 'max' in spec and value > spec['max']: fail(path, 'above maximum')
    if kind in ('string', 'array') and len(value) > spec.get('max_length', 4096 if kind == 'string' else 256):
        fail(path, 'too long')
    if kind == 'array':
        for i, item in enumerate(value): check(item, spec['items'], f'{path}[{i}]')
    if kind == 'object':
        fields = spec['properties']
        if set(value)-set(fields): fail(path, 'unknown object fields')
        for name, child in fields.items():
            if name not in value:
                if child.get('required', False): fail(path+'.'+name, 'required')
            else: check(value[name], child, path+'.'+name)
    encoded(value)


def validate_type(spec, path, depth=0):
    if depth > 4 or not isinstance(spec, dict): fail(path, 'invalid or overly nested type')
    common = {'type', 'required'}
    kind = spec.get('type')
    if not isinstance(kind, str) or kind not in TYPES: fail(path, 'unknown type')
    allowed = common | {'string':{'max_length'}, 'integer':{'min','max'}, 'number':{'min','max'},
                        'boolean':set(), 'enum':{'enum'}, 'object':{'properties'}, 'array':{'items','max_length'}}[kind]
    if set(spec)-allowed: fail(path, 'unknown type options')
    if 'required' in spec and type(spec['required']) is not bool: fail(path, 'required must be boolean')
    for key in ('min', 'max'):
        if key in spec and (type(spec[key]) not in (int,float) or (type(spec[key]) is float and not math.isfinite(spec[key]))):
            fail(path, 'bounds must be finite numbers')
    if 'min' in spec and 'max' in spec and spec['min'] > spec['max']: fail(path, 'reversed bounds')
    if 'max_length' in spec and (type(spec['max_length']) is not int or not 0 <= spec['max_length'] <= 4096): fail(path, 'invalid max_length')
    if kind == 'enum':
        choices = spec.get('enum')
        if not isinstance(choices, list) or not 1 <= len(choices) <= 64: fail(path, 'enum requires choices')
        if any(type(x) not in (str,int,float,bool) or (isinstance(x,str) and '\x00' in x) for x in choices): fail(path, 'enum choices must be scalar')
        if len({(type(x).__name__, encoded(x)) for x in choices}) != len(choices): fail(path, 'duplicate enum choices')
    if kind == 'array': validate_type(spec.get('items'), path+'[]', depth+1)
    if kind == 'object':
        fields = spec.get('properties')
        if not isinstance(fields, dict) or len(fields)>64: fail(path, 'object requires bounded properties')
        for name, child in fields.items():
            if not isinstance(name,str) or not BUSINESS_NAME.fullmatch(name): fail(path, 'invalid property name')
            validate_type(child, path+'.'+name, depth+1)


def validate_schema(schema):
    encoded(schema)
    if not isinstance(schema, dict) or set(schema) != {'version','fields'} or type(schema['version']) is not int or schema['version'] != VERSION:
        raise ValueError('unsupported parameter schema version or fields')
    fields = schema['fields']
    if not isinstance(fields, dict) or len(fields)>64: raise ValueError('schema fields must be a bounded object')
    targets = set()
    metadata = {'label','description','default','overridable','binding'}
    for name, field in fields.items():
        if not isinstance(name,str) or not BUSINESS_NAME.fullmatch(name) or name in RESERVED or name.startswith('MINI_EVAL_'):
            raise ValueError('invalid or platform-reserved parameter name')
        if not isinstance(field, dict): fail(name, 'field must be an object')
        spec = {k:v for k,v in field.items() if k not in metadata}
        validate_type(spec, name)
        for key in ('label','description'):
            if key in field and (not isinstance(field[key],str) or len(field[key])>1000): fail(name, 'invalid '+key)
        if 'overridable' in field and type(field['overridable']) is not bool: fail(name, 'overridable must be boolean')
        if 'default' in field: check(field['default'], spec, name)
        binding = field.get('binding', {'target':'input'})
        if not isinstance(binding,dict): fail(name, 'invalid binding')
        target = binding.get('target')
        if target == 'input':
            if set(binding) != {'target'}: fail(name, 'invalid input binding')
            continue
        if target not in ('argv','env') or set(binding)-{'target','name','encoding'}: fail(name, 'invalid binding')
        dest = binding.get('name'); encoding = binding.get('encoding','literal')
        if not isinstance(dest,str): fail(name, 'binding name is required')
        if target == 'env':
            environment({dest:''})
        elif not re.fullmatch(r'--[A-Za-z][A-Za-z0-9_-]{0,63}',dest): fail(name, 'invalid option name')
        key=(target,dest)
        if key in targets: fail(name, 'duplicate binding target')
        targets.add(key)
        if encoding not in ('literal','json','flag','repeat'): fail(name, 'unknown encoding')
        kind=field['type']
        if encoding=='flag' and (target!='argv' or kind!='boolean'): fail(name, 'flag requires argv boolean')
        if encoding=='repeat' and (target!='argv' or kind!='array' or field['items']['type'] in ('array','object')): fail(name, 'repeat requires argv scalar array')
        if kind in ('object','array') and encoding not in ('json','repeat'): fail(name, 'structured values require explicit encoding')
    return deepcopy(schema)


def resolve_layers(schema, layers=(), overrides=None):
    """Resolve named, partial layers without mutating them; objects replace whole.

    Required fields are checked only after all layers; supplied values are checked
    at each layer so an invalid value cannot be concealed by a later override.
    """
    schema=validate_schema(schema)
    overrides={} if overrides is None else overrides
    if not isinstance(overrides,dict): raise ValueError('parameter overrides must be an object')
    fields=schema['fields']
    if set(overrides)-set(fields): raise ValueError('unknown business parameter fields')
    for name in overrides:
        if not fields[name].get('overridable',False): fail(name, 'runtime override is not allowed')
    result={k:deepcopy(f['default']) for k,f in fields.items() if 'default' in f}
    sources={k:'template' for k in result}
    seen={'template','override'}
    for source,values,is_override in [(k,v,False) for k,v in layers]+[('override',overrides,True)]:
        if not isinstance(source,str) or not source or len(source)>100: raise ValueError('invalid parameter source')
        if not is_override and source in seen: raise ValueError('duplicate or reserved parameter source')
        seen.add(source)
        if not isinstance(values,dict): raise ValueError('parameter values must be objects')
        if set(values)-set(fields): raise ValueError('unknown business parameter fields')
        for name,value in values.items():
            check(value,fields[name],source+'.'+name)
            result[name]=deepcopy(value);sources[name]=source
    for name, field in fields.items():
        if name not in result:
            if field.get('required',False): fail(name,'required')
        else: check(result[name],field,name)
    encoded(result)
    return result,sources


def resolve_values(schema, values=None, overrides=None):
    return resolve_layers(schema,[('values',{} if values is None else values)],overrides)[0]


def bind_values(schema, values, command, env=None):
    """Append typed arguments; JSON input remains canonical and is not modified."""
    schema=validate_schema(schema); values=resolve_values(schema,values)
    if not isinstance(command,list) or not command or any(not isinstance(x,str) or '\x00' in x for x in command): raise ValueError('command must be argv')
    argv=list(command); additions=dict(env or {})
    def literal(value): return encoded(value) if type(value) is not str else value
    for name,field in schema['fields'].items():
        binding=field.get('binding',{'target':'input'})
        if name not in values or binding['target']=='input': continue
        value=values[name]; dest=binding['name']; encoding=binding.get('encoding','literal')
        if binding['target']=='env':
            if dest in additions: fail(name,'environment binding conflicts with runtime configuration')
            additions[dest]=encoded(value) if encoding=='json' else literal(value)
        elif encoding=='flag':
            if value: argv.append(dest)
        elif encoding=='repeat':
            for item in value: argv.extend([dest,literal(item)])
        else: argv.extend([dest,encoded(value) if encoding=='json' else literal(value)])
    if len(argv)>512 or sum(len(x.encode()) for x in argv)>65536: raise ValueError('bound command exceeds limit')
    environment(additions)
    return argv, additions
