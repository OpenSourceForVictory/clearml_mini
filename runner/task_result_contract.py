"""Bounded task-level result contract, separate from process exit status."""
import json
import math
import re

MAX_BYTES = 16 * 1024
MAX_METRICS = 64
KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,63}$')


def validate(value):
    if not isinstance(value, dict) or set(value) - {'success', 'metrics', 'summary'}:
        raise ValueError('task result must contain success, metrics, and optional summary')
    if type(value.get('success')) is not bool:
        raise ValueError('task result success must be a boolean')
    metrics = value.get('metrics', {})
    if not isinstance(metrics, dict) or len(metrics) > MAX_METRICS:
        raise ValueError('task result metrics must be a bounded object')
    for key, metric in metrics.items():
        if (not isinstance(key, str) or not KEY.fullmatch(key) or type(metric) not in (int, float)
                or not math.isfinite(metric)):
            raise ValueError('task result metrics must use valid names and finite numeric values')
    summary = value.get('summary')
    if summary is not None and (not isinstance(summary, str) or len(summary) > 500):
        raise ValueError('task result summary must be at most 500 characters')
    result = {'success': value['success'], 'metrics': metrics}
    if summary is not None:
        result['summary'] = summary
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) > MAX_BYTES:
        raise ValueError('task result exceeds 16 KiB')
    return result
