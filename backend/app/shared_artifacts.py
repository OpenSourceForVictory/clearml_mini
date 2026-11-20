"""Shared output browsing. DB scopes ownership; isolated I/O returns file bytes."""
import base64
import hashlib
import os
import time
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

import jwt
from fastapi import Depends, HTTPException, Request, Response, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .core import SharedArtifactRoot, ExecutionData, EpisodeRecord, Run, Job, Slot, Worker, User
from .execution_locations import visible_run, reported_path
from .artifact_media import byte_range, media_type
from . import shared_storage, shared_file_io
from worker.artifact_reader import parts, ReadError, BLOCK_BYTES


class BoundedStream(StreamingResponse):
    """Keep admission until response completion, disconnect or cancellation."""
    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


class Registration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    output_root: str = Field(max_length=4096)
    layout: Literal["legacy","batch-v1"] = "legacy"


class FileQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=4096)
    version: str = Field(max_length=256)


def safe_path(path):
    try:
        parts(path)
    except ReadError:
        raise HTTPException(400, '非法文件相对路径') from None
    return path


def save_root(s, run, node_name, output, layout="legacy"):
    mapping, relative = shared_storage.match(node_name, output)
    snapshot = dict(storage_id=mapping['id'], marker=mapping['marker'], relative=relative,
                    node_name=node_name, output_root=output)
    if layout != "legacy":
        snapshot["layout"]=layout
        snapshot["artifact_root"]=str(PurePosixPath(output).parents[2])
    # Serialize only a first registration for this attempt, never the scheduler.
    if not s.scalar(select(Run.id).where(Run.id == run.id).with_for_update()):
        raise HTTPException(404, 'execution not found')
    row = s.get(SharedArtifactRoot, run.id)
    if row:
        if row.snapshot.get('state') == 'planned':
            planned=row.snapshot
            if (row.execution_id != run.execution_id or planned['output_root']!=output
                    or planned['node_name']!=node_name or planned['layout']!=layout):
                raise HTTPException(409, '执行输出目录与调度计划不一致')
            snapshot['artifact_root']=planned['artifact_root']
            row.snapshot=snapshot;s.commit()
            return snapshot
        if row.execution_id != run.execution_id or any(row.snapshot.get(k)!=v for k,v in snapshot.items()):
            raise HTTPException(409, '执行输出目录已登记，不能重新绑定')
        return row.snapshot
    s.add(SharedArtifactRoot(run_id=run.id, execution_id=run.execution_id, snapshot=snapshot))
    s.commit()
    return snapshot


def snapshot(s, run):
    row = s.get(SharedArtifactRoot, run.id)
    if row:
        if row.execution_id != run.execution_id:
            raise HTTPException(409, '执行目录身份不匹配')
        if row.snapshot.get('state')=='planned':
            return save_root(s,run,row.snapshot['node_name'],row.snapshot['output_root'],row.snapshot['layout'])
        return row.snapshot
    records = s.scalars(select(ExecutionData).where(ExecutionData.run_id == run.id,
        ExecutionData.slot_id == run.slot_id, ExecutionData.execution_id == run.execution_id)).all()
    roots = set()
    for record in records:
        if (record.identity or {}).get('location', 'runs') != 'runs':
            continue
        path = reported_path(record.root_path, record.entry_name, run.execution_id)
        if path:
            roots.add((record.worker_id, path + '/output'))
    if len(roots) != 1:
        raise HTTPException(409, '执行输出目录未上报或存在歧义，不能从当前节点配置推断历史目录')
    worker_id, path = next(iter(roots))
    worker = s.get(Worker, worker_id)
    if not worker:
        raise HTTPException(409, '执行来源节点记录不存在')
    return save_root(s, run, worker.node_name, path)


def plan(s,run,worker,slot,job,fresh):
    """Freeze layout at first claim; retries of a claim never use new node intent."""
    row=s.get(SharedArtifactRoot,run.id)
    if row is None and fresh and job.benchmark in ('cs-client','inference-service'):
        from .storage import desired
        config=desired(worker,slot)['config'] or (slot.storage_report or {}).get('effective') or {}
        root=config.get('artifact_root')
        if root:
            if 'artifact_roots_v1' not in (slot.software or {}).get('capabilities',[]):
                raise HTTPException(409,'independent artifact directories require Worker upgrade')
            path=str(PurePosixPath(root)/(job.batch_id or 'standalone')/job.id/run.execution_id)
            row=SharedArtifactRoot(run_id=run.id,execution_id=run.execution_id,snapshot=dict(
                state='planned',layout='batch-v1',artifact_root=root,output_root=path,node_name=worker.node_name))
            s.add(row);s.flush()
    if row and row.snapshot.get('layout')=='batch-v1':
        return {'artifact_layout':'batch-v1','artifact_root':row.snapshot['artifact_root'],'artifact_transport':'shared'}
    return {}


def bindings(s, run_id):
    root=s.get(SharedArtifactRoot,run_id)
    independent=bool(root and root.snapshot.get('layout')=='batch-v1')
    linked = {}
    for episode in s.scalars(select(EpisodeRecord).where(EpisodeRecord.run_id == run_id)):
        for path in (episode.result or {}).get('artifacts', []):
            if isinstance(path, str):
                if independent and not path.startswith(('output/','episodes/')):path='output/'+path
                linked.setdefault(path, []).append(episode.episode_id)
    return linked


def install(app, db, current, worker_auth):
    root = '/api/v1/jobs/{job_id}/attempts/{run_id}/shared-artifacts'

    @app.post('/api/v1/worker/executions/{execution_id}/shared-artifacts')
    def register(execution_id: str, body: Registration, worker=Depends(worker_auth), s=Depends(db)):
        run = s.scalar(select(Run).where(Run.execution_id == execution_id))
        slot = s.get(Slot, run.slot_id) if run else None
        if not slot or slot.worker_id != worker.id:
            raise HTTPException(404, 'execution not found')
        job=s.get(Job,run.job_id)
        suffix=('/'+(job.batch_id or 'standalone')+'/'+job.id+'/'+execution_id
                if body.layout=='batch-v1' else '/runs/'+execution_id+'/output')
        if not body.output_root.endswith(suffix):
            raise HTTPException(400, '输出目录必须属于本次执行')
        save_root(s, run, worker.node_name, body.output_root, body.layout)
        return {'registered': True, 'transport': 'shared'}

    @app.get(root)
    async def listing(job_id: str, run_id: str, path: str = Query(default='', max_length=4096),
                      after: str = Query(default='', max_length=255), version: str | None = Query(default=None, max_length=512),
                      user=Depends(current), s=Depends(db)):
        run = visible_run(s, job_id, run_id, user)
        if not shared_storage.mappings():
            return {'enabled': False, 'items': []}
        safe_path(path)
        location = snapshot(s, run)
        mapping = shared_storage.resolve(location)
        linked, user_id = bindings(s, run.id), user.id
        s.close()  # No connection/transaction survives shared filesystem I/O.
        result = await shared_file_io.read(mapping, location['relative'],
            dict(operation='list', path=path, after=after, version=version), user_id)
        for entry in result['items']:
            entry['path'] = str(PurePosixPath(path) / entry['name'])
            entry['episode_ids'] = linked.get(entry['path'], entry.get('episode_ids', []))
            entry['media_type'] = media_type(entry['name'])
        return {**result, 'enabled': True, 'path': path, 'storage_id': location['storage_id'],
                'node_name': location['node_name'], 'output_root': location['output_root']}

    @app.post(root + '/media-access')
    async def grant(job_id: str, run_id: str, body: FileQuery, request: Request, response: Response,
                    user=Depends(current), s=Depends(db)):
        run = visible_run(s, job_id, run_id, user)
        safe_path(body.path)
        location = snapshot(s, run)
        mapping, user_id = shared_storage.resolve(location), user.id
        token = request.headers.get('authorization', '').partition(' ')[2]
        try:
            original = jwt.decode(token, os.environ['JWT_SECRET'], algorithms=['HS256'])
            expires = min(int(original['exp']), int(time.time()) + 300)
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise HTTPException(401, 'invalid session') from None
        s.close()
        info = await shared_file_io.read(mapping, location['relative'],
            dict(operation='stat', path=body.path, version=body.version), user_id)
        identity = hashlib.sha256((body.path + '\0' + info['version']).encode()).hexdigest()
        path = f'/api/v1/jobs/{job_id}/attempts/{run_id}/shared-artifacts/media/{identity}'
        claims = dict(sub=user_id, aud='shared-artifact', exp=expires, job=job_id, run=run_id,
                      path=body.path, version=info['version'], identity=identity)
        response.set_cookie('mini-eval-shared-artifact', jwt.encode(claims, os.environ['JWT_SECRET'], algorithm='HS256'),
                            max_age=max(0, expires-int(time.time())), httponly=True, secure=request.url.scheme == 'https',
                            samesite='strict', path=path)
        return {'url': path, 'expires_at': expires, 'media_type': media_type(body.path)}

    @app.api_route(root + '/media/{identity}', methods=['GET', 'HEAD'])
    async def media(job_id: str, run_id: str, identity: str, request: Request, s=Depends(db)):
        try:
            claims = jwt.decode(request.cookies.get('mini-eval-shared-artifact', ''), os.environ['JWT_SECRET'],
                algorithms=['HS256'], audience='shared-artifact', options={'require': ['sub', 'exp', 'aud', 'path', 'version', 'identity', 'job', 'run']})
            if (claims.get('job'), claims.get('run'), claims.get('identity')) != (job_id, run_id, identity):
                raise ValueError()
        except (jwt.PyJWTError, ValueError):
            raise HTTPException(401, '文件授权已过期或无效，请重新加载预览') from None
        user = s.get(User, claims['sub'])
        if not user or not user.active:
            raise HTTPException(401, 'invalid user')
        run = visible_run(s, job_id, run_id, user)
        location = snapshot(s, run)
        mapping, user_id = shared_storage.resolve(location), user.id
        s.close()
        query = dict(path=claims['path'], version=claims['version'])
        info = await shared_file_io.read(mapping, location['relative'], dict(operation='stat', **query), user_id)
        size = info['size_bytes']
        etag = '"' + hashlib.sha256(info['version'].encode()).hexdigest() + '"'
        requested = request.headers.get('range') if request.method == 'GET' else None
        if request.headers.get('if-range') not in (None, etag):
            requested = None
        first, last, partial = byte_range(requested, size)
        headers = {'Content-Length': str(max(0, last-first+1)), 'Accept-Ranges': 'bytes', 'ETag': etag,
                   'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Disposition': ("inline" if media_type(claims['path']) != 'application/octet-stream' else 'attachment')
                       + "; filename*=UTF-8''" + quote(claims['path'].split('/')[-1], safe='')}
        if partial:
            headers['Content-Range'] = f'bytes {first}-{last}/{size}'
        if request.method == 'HEAD' or not size:
            return Response(b'', status_code=206 if partial else 200, headers=headers, media_type=media_type(claims['path']))
        async def chunk(offset):
            if time.time() >= claims['exp']:
                raise HTTPException(401, '文件授权已过期')
            result = await shared_file_io.read(mapping, location['relative'],
                dict(operation='read', offset=offset, length=min(BLOCK_BYTES, last-offset+1), **query), user_id)
            data = base64.b64decode(result['data'])
            if not data:
                raise HTTPException(409, '文件已变化')
            return data
        release = shared_file_io.reserve(user_id, streams=True)
        try:
            initial = await chunk(first)  # Fail before sending HTTP headers when possible.
        except BaseException:
            release()
            raise
        async def stream():
            offset, data = first, initial
            while True:
                yield data
                offset += len(data)
                if offset > last or await request.is_disconnected():
                    break
                data = await chunk(offset)
        return BoundedStream(stream(), release=release, status_code=206 if partial else 200,
                                 media_type=media_type(claims['path']), headers=headers)
