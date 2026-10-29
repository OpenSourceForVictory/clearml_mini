"""Shared validation with no external dependencies, used by SDK and API."""
import json
import math
from .cs_contract import artifact_name


def validate_event(event):
    if not isinstance(event, dict):
        raise ValueError('episode event must be an object')
    allowed = {'episode_id', 'seed', 'status', 'success', 'metrics', 'artifacts', 'runtime', 'error'}
    if set(event) - allowed:
        raise ValueError('unknown episode event fields')
    for key, lower, upper in [('episode_id', 1, 10000), ('seed', 0, 2147483647)]:
        if type(event.get(key)) is not int or not lower <= event[key] <= upper:
            raise ValueError('invalid '+key)
    status = event.get('status')
    required = {'episode_id', 'seed', 'status'}
    if status == 'COMPLETED':
        required |= {'success', 'metrics', 'runtime', 'artifacts'}
        if type(event.get('success')) is not bool:
            raise ValueError('success must be boolean')
        metrics = event.get('metrics')
        if not isinstance(metrics, dict) or len(metrics) > 64:
            raise ValueError('metrics must be a bounded object')
        for k, v in metrics.items():
            if not isinstance(k, str) or not 1 <= len(k) <= 100 or type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError('metrics must contain finite numeric values')
        runtime = event.get('runtime')
        if type(runtime) not in (int, float) or not math.isfinite(runtime) or runtime < 0:
            raise ValueError('invalid runtime')
        artifacts = event.get('artifacts')
        if not isinstance(artifacts, list) or len(artifacts) > 8 or any(not isinstance(x, str) or not artifact_name(x) for x in artifacts):
            raise ValueError('invalid artifact references')
    elif status == 'ERROR':
        required.add('error')
        if not isinstance(event.get('error'), str) or not 1 <= len(event['error']) <= 1000:
            raise ValueError('invalid error')
    elif status != 'STARTED':
        raise ValueError('invalid episode status')
    if set(event) != required or len(json.dumps(event, allow_nan=False).encode()) > 16384:
        raise ValueError('invalid episode payload')
    return event
