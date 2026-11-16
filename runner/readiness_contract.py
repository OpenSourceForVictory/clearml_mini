"""Versioned, dependency-free service probe contract shared by API and Worker."""
from copy import deepcopy
import json
import math
import re

KINDS = {'tcp', 'http', 'websocket'}
SENSITIVE = {'authorization', 'proxy-authorization', 'cookie', 'set-cookie', 'x-api-key'}
DEFAULTS = dict(version=1, kind='http', port='business', path='/healthz', tls=False,
                headers={}, headers_from_env={}, ca_env=None, subprotocols=[], mode='handshake',
                send_text=None, expect=None, statuses=list(range(200, 300)), interval=5,
                timeout=2, startup_timeout=120, success_threshold=1, failure_threshold=3,
                stale_after=20)


def validate(value):
    if not isinstance(value, dict) or set(value)-set(DEFAULTS):
        raise ValueError('readiness: unknown configuration fields')
    out = {**deepcopy(DEFAULTS), **deepcopy(value)}
    if type(out['version']) is not int or out['version'] != 1 or out['kind'] not in KINDS:
        raise ValueError('readiness: unsupported version or kind')
    if type(out['tls']) is not bool:
        raise ValueError('readiness.tls: expected boolean')
    port = out['port']
    if port != 'business' and (type(port) is not int or not 1024 <= port <= 65535):
        raise ValueError('readiness.port: expected business or port 1024..65535')
    path = out['path']
    if not isinstance(path, str) or len(path) > 500 or not path.startswith('/') or path.startswith('//') or any(ord(c) < 33 or ord(c) == 127 for c in path) or '#' in path:
        raise ValueError('readiness.path: expected absolute path without fragment or whitespace')
    for key, low, high in [('interval', .5, 300), ('timeout', .1, 60), ('startup_timeout', 5, 1800), ('stale_after', 1, 1200)]:
        v = out[key]
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('readiness.'+key+': out of range')
    if out['stale_after'] < 2*out['interval']+out['timeout']:
        raise ValueError('readiness.stale_after: must cover two intervals and timeout')
    for key in ('success_threshold', 'failure_threshold'):
        if type(out[key]) is not int or not 1 <= out[key] <= 20:
            raise ValueError('readiness.'+key+': expected integer 1..20')
    for key in ('headers', 'headers_from_env'):
        items = out[key]
        if not isinstance(items, dict) or len(items) > 16:
            raise ValueError('readiness.'+key+': expected at most 16 headers')
        for name, v in items.items():
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', name) or not isinstance(v, str) or len(v) > 4096 or any(ord(c)<32 or ord(c)==127 for c in v):
                raise ValueError('readiness.'+key+': invalid header')
            if name.lower() in {'host', 'connection', 'upgrade', 'content-length', 'transfer-encoding'} or name.lower().startswith('sec-websocket-'):
                raise ValueError('readiness: transport headers cannot be overridden')
            if key == 'headers' and name.lower() in SENSITIVE:
                raise ValueError('readiness.headers: sensitive header requires an environment reference')
            if key == 'headers_from_env' and not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,99}', v):
                raise ValueError('readiness.headers_from_env: invalid reference')
    names = [k.lower() for key in ('headers', 'headers_from_env') for k in out[key]]
    if len(set(names)) != len(names):
        raise ValueError('readiness: duplicate header')
    if out['ca_env'] is not None and (not isinstance(out['ca_env'], str) or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,99}', out['ca_env'])):
        raise ValueError('readiness.ca_env: invalid reference')
    if not isinstance(out['subprotocols'], list) or len(out['subprotocols']) > 8 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',x) for x in out['subprotocols']):
        raise ValueError('readiness.subprotocols: invalid list')
    if out['mode'] not in ('handshake','message'):
        raise ValueError('readiness.mode: invalid mode')
    if out['send_text'] is not None and (not isinstance(out['send_text'],str) or len(out['send_text'].encode())>4096):
        raise ValueError('readiness.send_text: expected at most 4096 bytes')
    statuses=out['statuses']
    if not isinstance(statuses,list) or not statuses or len(statuses)>100 or any(type(x) is not int or not 200<=x<=599 or 300<=x<400 for x in statuses):
        raise ValueError('readiness.statuses: expected non-redirect HTTP statuses')
    e=out['expect']
    if e is not None:
        if not isinstance(e,dict) or set(e)-{'type','pointer','value'} or 'value' not in e:
            raise ValueError('readiness.expect: invalid matcher')
        if e.get('type')=='text_equals':
            if not isinstance(e['value'],str) or 'pointer' in e:raise ValueError('readiness.expect: expected text value')
        elif e.get('type')=='json_equals':
            p=e.get('pointer')
            if not isinstance(p,str) or (p and not p.startswith('/')) or re.search(r'~(?![01])',p):raise ValueError('readiness.expect: invalid JSON pointer')
            if type(e['value']) not in (str,int,float,bool,type(None)):raise ValueError('readiness.expect: expected scalar')
        else:raise ValueError('readiness.expect: unsupported matcher')
        try: size=len(json.dumps(e,allow_nan=False).encode())
        except (ValueError,TypeError):raise ValueError('readiness.expect: invalid value') from None
        if size>8192:raise ValueError('readiness.expect: matcher too large')
    if out['kind']=='websocket' and out['mode']=='message' and e is None:
        raise ValueError('readiness.expect: message check requires a matcher')
    if out['kind']=='websocket' and out['mode']=='handshake' and (e is not None or out['send_text'] is not None):
        raise ValueError('readiness: handshake check cannot send or match messages')
    if out['kind']!='websocket' and (out['send_text'] is not None or out['subprotocols'] or out['mode']!='handshake'):
        raise ValueError('readiness: WebSocket options require websocket kind')
    if out['kind']=='tcp' and (e is not None or out['headers'] or out['headers_from_env'] or out['tls'] or out['ca_env']):
        raise ValueError('readiness: TCP checks only verify connectivity')
    return out


def normalize(definition):
    """Never mutate historical snapshots; preserve legacy status/freshness semantics."""
    if definition.get('readiness') is not None:
        return validate(definition['readiness'])
    return {**deepcopy(DEFAULTS), 'kind':definition.get('health_type','http'),
            'path':definition.get('health_path','/healthz'),
            'tls':definition.get('protocol') in ('https','wss'),
            'startup_timeout':definition.get('startup_timeout',120),
            'timeout':1, 'interval':2, 'failure_threshold':1, 'stale_after':15}


def matches(expect, raw):
    if expect is None:return True
    try:
        text=raw.decode('utf-8') if isinstance(raw,bytes) else raw
        if expect['type']=='text_equals':return text==expect['value']
        value=json.loads(text)
        for segment in expect['pointer'].split('/')[1:] if expect['pointer'] else []:
            key=segment.replace('~1','/').replace('~0','~')
            value=value[int(key)] if isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',key) else value[key]
        wanted=expect['value']
        return type(value) is type(wanted) and value==wanted
    except (ValueError,TypeError,KeyError,IndexError):return False
