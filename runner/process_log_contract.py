"""Versioned identity and bounds shared by the API, Worker and Runner."""
import base64
import json
from pathlib import PurePosixPath
from uuid import UUID

CAPABILITY = 'process_logs_v1'
VERSION = 1
SCOPES = ('task', 'business', 'episode', 'service')
PAGE_BYTES = 65536
RECORD_BYTES = 8192
DEFAULT_POLICY = dict(segment_bytes=4*1024**2, scope_bytes=16*1024**2,
                      execution_bytes=256*1024**2, node_bytes=5*1024**3)


def identity(execution_id, scope, episode_id=0):
    if str(UUID(execution_id)) != execution_id or scope not in SCOPES:
        raise ValueError('invalid log identity')
    if type(episode_id) is not int or not (1 <= episode_id <= 10000 if scope == 'episode' else episode_id == 0):
        raise ValueError('invalid log episode')
    return f'{execution_id}/{scope}/{episode_id}'


def relative_directory(scope, episode_id=0):
    identity('00000000-0000-0000-0000-000000000000', scope, episode_id)
    return f'episodes/{episode_id:06d}' if scope == 'episode' else scope


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
        raise ValueError('invalid log budget fields')
    limits = [value[k] for k in DEFAULT_POLICY]
    if any(type(x) is not int for x in limits) or not (65536 <= limits[0] <= limits[1] <= limits[2] <= limits[3] <= 1024**5):
        raise ValueError('invalid log budget order or size')
    return dict(value)


def absolute_directory(value):
    if not isinstance(value, str) or not value.startswith('/') or len(value)>4096:
        raise ValueError('invalid log directory')
    if any(ord(c)<32 or ord(c)==127 for c in value) or '..' in value.split('/') or str(PurePosixPath(value)) != value or value.startswith('//'):
        raise ValueError('invalid log directory')
    return value


def cursor(log_key, generation, sequence):
    if type(sequence) is not int or sequence<0 or sequence>2**63-1:
        raise ValueError('invalid log cursor')
    return base64.urlsafe_b64encode(json.dumps([log_key, str(UUID(generation)), sequence], separators=(',', ':')).encode()).decode()


def parse_cursor(value, log_key, generation):
    try:
        if not isinstance(value,str) or len(value)>512:raise ValueError()
        data=json.loads(base64.b64decode(value,altchars=b'-_',validate=True))
        if not isinstance(data,list) or len(data)!=3 or data[:2]!=[log_key,generation]:raise ValueError()
        if type(data[2]) is not int or not 0<=data[2]<=2**63-1:raise ValueError()
        return data[2]
    except (ValueError,TypeError,UnicodeError) as exc:
        raise ValueError('invalid or superseded log cursor') from exc
