"""Administrator-defined shared filesystem mappings; never browser paths."""
import json
import os
import re
from pathlib import PurePosixPath

from fastapi import HTTPException
from worker.artifact_reader import parts, ReadError


def mappings():
    try:
        values = json.loads(os.getenv('MINI_EVAL_SHARED_STORAGE', '[]'))
        if not isinstance(values, list) or len(values) > 32:
            raise ValueError()
        ids = set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {'id', 'node_names', 'node_root', 'api_root', 'marker'}:
                raise ValueError()
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['id']) or value['id'] in ids:
                raise ValueError()
            ids.add(value['id'])
            if not isinstance(value['node_names'], list) or not value['node_names'] or any(not isinstance(n, str) or not n for n in value['node_names']):
                raise ValueError()
            for key in ('node_root', 'api_root'):
                parts(value[key], absolute=True)
            if not isinstance(value['marker'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value['marker']):
                raise ValueError()
        return values
    except (ValueError, TypeError, KeyError, ReadError):
        raise HTTPException(503, '共享存储映射配置无效，请联系管理员') from None


def match(node, path):
    try:
        parts(path, absolute=True)
    except ReadError:
        raise HTTPException(400, '执行输出目录不合法') from None
    target = PurePosixPath(path)
    candidates = [m for m in mappings() if node in m['node_names']
                  and target.is_relative_to(PurePosixPath(m['node_root']))]
    if len(candidates) != 1:
        raise HTTPException(409, '执行输出目录没有唯一的共享存储映射')
    mapping = candidates[0]
    relative = str(target.relative_to(mapping['node_root']))
    if relative == '.':
        raise HTTPException(409, '执行输出目录不能是整个共享根')
    return mapping, relative


def resolve(snapshot):
    candidates = [m for m in mappings() if m['id'] == snapshot['storage_id']]
    if len(candidates) != 1 or candidates[0]['marker'] != snapshot['marker']:
        raise HTTPException(409, '此执行的共享存储授权已撤销或身份已变更')
    mapping = candidates[0]
    if snapshot['node_name'] not in mapping['node_names']:
        raise HTTPException(403, '此节点的共享存储授权已撤销')
    return mapping
