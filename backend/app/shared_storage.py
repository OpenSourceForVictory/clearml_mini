"""Administrator-defined shared filesystem mappings; never browser paths."""
import hashlib
import json
import os
import re
from pathlib import PurePosixPath

from fastapi import HTTPException
from worker.artifact_reader import parts, ReadError


def mappings():
    try:
        explicit = os.getenv('MINI_EVAL_SHARED_STORAGE', '').strip()
        root = os.getenv('SHARED_ARTIFACT_HOST_ROOT', '')
        if not explicit and root:
            parts(root, absolute=True)
            root = str(PurePosixPath(root))
            digest = hashlib.sha256(root.encode()).hexdigest()[:32]
            return [dict(id='auto-' + digest, node_names=[], node_root=root,
                         api_root='/mnt/mini-eval-artifacts', marker='auto-' + digest,
                         automatic=True)]
        values = json.loads(explicit or '[]')
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


def allows_node(mapping, node):
    return bool(mapping.get('automatic')) or node in mapping['node_names']


def match(node, path):
    try:
        parts(path, absolute=True)
    except ReadError:
        raise HTTPException(400, '执行输出目录不合法') from None
    target = PurePosixPath(path)
    candidates = [m for m in mappings() if allows_node(m, node)
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
    if not allows_node(mapping, snapshot['node_name']):
        raise HTTPException(403, '此节点的共享存储授权已撤销')
    return mapping
