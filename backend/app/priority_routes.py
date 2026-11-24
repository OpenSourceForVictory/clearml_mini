"""Explicit route classification for the opt-in priority API deployment.

The manifest uses FastAPI route patterns, not URL substrings. Keep this module
independent of app.main so route coverage can be checked without an import loop.
"""
import json
from functools import lru_cache
from pathlib import Path
import re


ROLES = {
    'health': None,
    'execution_control': 'execution-control',
    'user_action': 'interaction',
    'user_long': 'interaction',
    'page_read': 'interaction',
    'worker_claim': 'worker-data',
    'worker_result': 'worker-data',
    'worker_observation': 'worker-data',
    'bulk_transfer': 'bulk-transfer',
    'fallback': 'interaction',
}
LEGACY_CATEGORIES = {'ordinary', 'control', 'observation', 'result'}


@lru_cache(maxsize=1)
def manifest():
    entries = json.loads(Path(__file__).with_name('priority_route_manifest.json').read_text())
    return compile_manifest(entries)


def compile_manifest(entries):
    seen = set()
    routes = []
    for item in entries:
        if set(item) != {'method', 'path', 'category', 'legacy_category'}:
            raise ValueError('invalid priority route entry')
        method, path, category, legacy = (item['method'], item['path'], item['category'],
                                          item['legacy_category'])
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'} or not path.startswith('/'):
            raise ValueError('invalid priority route method or path')
        if category not in ROLES or category == 'fallback':
            raise ValueError('invalid priority route category')
        if legacy not in LEGACY_CATEGORIES:
            raise ValueError('invalid legacy route category')
        if (method, path) in seen:
            raise ValueError('duplicate priority route entry')
        seen.add((method, path))
        pattern = '^' + re.sub(r'\{[^/{}]+\}', '[^/]+', re.escape(path).replace('\\{', '{').replace('\\}', '}')) + '$'
        routes.append((method, path, category, legacy, re.compile(pattern)))
    return tuple(routes)


@lru_cache(maxsize=4096)
def _category(path, method, index, fallback):
    matches = [item[index] for item in manifest()
               if item[0] == method and item[-1].fullmatch(path)]
    if len(set(matches)) > 1:
        raise ValueError('ambiguous priority route')
    return matches[0] if matches else fallback


def category_for(path, method):
    """Unknown requests get only restricted fallback capacity, never priority."""
    return _category(path, method, 2, 'fallback')


def legacy_category_for(path, method):
    """Preserve the original single/dual API behavior until opt-in cutover."""
    return _category(path, method, 3, 'ordinary')


def role_for(path, method):
    return ROLES[category_for(path, method)]


def accepts_role(role, path, method):
    expected = role_for(path, method)
    return expected is None or role == expected
