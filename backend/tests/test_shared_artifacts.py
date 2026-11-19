# ruff: noqa: F811
import asyncio
import json
import uuid
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.app import shared_storage, shared_file_io
from backend.app.core import ExecutionData, SharedArtifactRoot, Slot, User, EpisodeRecord
from backend.tests.test_cs_protocol import prepare
from backend.tests.test_execution import env  # noqa: F401


@pytest.fixture
def shared(env, tmp_path, monkeypatch):
    main, c, auth = env
    _, token, slot, job, task = prepare(env)
    root = tmp_path/'share'
    root.mkdir()
    (root/'.mini-eval-storage-id').write_text('test-shared-store-001\n')
    output = root/'machine'/'runs'/task['execution_id']/'output'
    output.mkdir(parents=True)
    (output/'result.json').write_text('{"success":true}')
    (output/'video.mp4').write_bytes(b'0123456789')
    (output/'empty.txt').touch()
    (output/'folder').mkdir()
    (output/'folder'/'nested.txt').write_text('nested')
    with main.SessionLocal() as s:
        owner = s.get(Slot, slot)
        node = owner.node_name
        s.add(ExecutionData(worker_id=owner.worker_id, slot_id=slot, run_id=task['run_id'],
            execution_id=task['execution_id'], root_id='root', root_path='/node/share/machine/runs',
            entry_name=task['execution_id'], generation=str(uuid.uuid4()), scan_status='complete'))
        s.commit()
    mapping = dict(id='test', marker='test-shared-store-001', node_names=[node],
                   node_root='/node/share', api_root=str(root))
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([mapping]))
    return main, c, auth, token, job, task, output, mapping


def route(shared):
    return f'/api/v1/jobs/{shared[4]}/attempts/{shared[5]["run_id"]}/shared-artifacts'


def grant(shared, name):
    _, c, auth, *_ = shared
    result = c.get(route(shared), headers=auth['alice'])
    assert result.status_code == 200, result.text
    item = next(item for item in result.json()['items'] if item['name'] == name)
    response = c.post(route(shared)+'/media-access', headers=auth['alice'],
                      json={'path':item['path'], 'version':item['version']})
    assert response.status_code == 200, response.text
    return response.json()['url']


def test_browse_preview_range_head_and_legacy(shared):
    main, c, auth, _, _, task, output, _ = shared
    response = c.get(route(shared), headers=auth['alice'])
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] and response.json()['storage_id'] == 'test'
    assert len(response.json()['items']) == 4
    with main.SessionLocal() as s:
        assert s.get(SharedArtifactRoot, task['run_id']).snapshot['relative'].endswith('/output')
    page = c.get(route(shared), params={'path':'folder'}, headers=auth['alice'])
    assert page.json()['items'][0]['name'] == 'nested.txt'
    url = grant(shared, 'video.mp4')
    response = c.get(url, headers={'Range':'bytes=2-5'})
    assert response.status_code == 206 and response.content == b'2345'
    assert response.headers['content-range'] == 'bytes 2-5/10'
    assert response.headers['content-type'] == 'video/mp4'
    etag = response.headers['etag']
    assert c.get(url, headers={'Range':'bytes=-3','If-Range':etag}).content == b'789'
    assert c.get(url, headers={'Range':'bytes=2-5','If-Range':'"old"'}).content == b'0123456789'
    head = c.head(url, headers={'Range':'bytes=2-5'})
    assert head.status_code == 200 and head.content == b'' and head.headers['content-length'] == '10'
    assert c.get(url, headers={'Range':'bytes=20-30'}).status_code == 416
    assert c.get(url, headers={'Range':'bytes=1-2,4-5'}).status_code == 416
    empty = grant(shared, 'empty.txt')
    assert c.get(empty).status_code == 200 and c.get(empty).content == b''
    assert c.get(empty, headers={'Range':'bytes=0-10'}).status_code == 416
    (output/'unsafe.svg').write_text('<svg onload="alert(1)"/>')
    assert c.get(grant(shared, 'unsafe.svg')).headers['content-disposition'].startswith('attachment;')


def test_auth_changes_missing_and_scope(shared, monkeypatch):
    main, c, auth, _, _, _, output, mapping = shared
    assert c.get(route(shared), headers=auth['bob']).status_code == 404
    assert c.get(route(shared)).status_code == 401
    url = grant(shared, 'video.mp4')
    assert c.get(url+'wrong').status_code == 401
    (output/'video.mp4').write_bytes(b'changed-longer')
    assert c.get(url).status_code == 409
    url = grant(shared, 'video.mp4')
    (output/'video.mp4').unlink()
    assert c.get(url).status_code == 410
    (output/'link').symlink_to('/etc/passwd')
    items = c.get(route(shared), headers=auth['alice']).json()['items']
    link = next(i for i in items if i['name']=='link')
    assert c.post(route(shared)+'/media-access', headers=auth['alice'], json={'path':'link','version':link['version']}).status_code == 403
    for path in ('../secrets','/etc/passwd','.mini-eval-protocol/file'):
        assert c.get(route(shared), headers=auth['alice'], params={'path':path}).status_code == 400
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([{**mapping,'marker':'different-store-001'}]))
    assert c.get(route(shared), headers=auth['alice']).status_code == 409
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([{**mapping,'node_names':['someone-else']}]))
    assert c.get(route(shared), headers=auth['alice']).status_code == 403
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', json.dumps([mapping]))
    url = grant(shared, 'result.json')
    with main.SessionLocal() as s:
        s.scalar(select(User).where(User.username=='alice')).active = False
        s.commit()
    assert c.get(url).status_code == 401


def test_storage_missing_wrong_marker_and_no_guess(shared, monkeypatch):
    main, c, auth, _, _, task, output, mapping = shared
    (Path(mapping['api_root'])/'.mini-eval-storage-id').write_text('wrong')
    assert c.get(route(shared), headers=auth['alice']).status_code == 503
    (Path(mapping['api_root'])/'.mini-eval-storage-id').write_text(mapping['marker'])
    assert c.get(route(shared), headers=auth['alice']).status_code == 200
    with main.SessionLocal() as s:
        s.delete(s.get(SharedArtifactRoot, task['run_id']))
        for row in s.scalars(select(ExecutionData).where(ExecutionData.run_id==task['run_id'])):s.delete(row)
        s.commit()
    assert c.get(route(shared), headers=auth['alice']).status_code == 409
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE','[]')
    assert c.get(route(shared), headers=auth['alice']).json()['enabled'] is False


def test_worker_registration_idempotent_and_protocol_bindings(shared):
    main, c, auth, token, _, task, output, _ = shared
    url = f'/api/v1/worker/executions/{task["execution_id"]}/shared-artifacts'
    headers = token
    body = {'output_root':f'/node/share/machine/runs/{task["execution_id"]}/output'}
    assert c.post(url, headers=headers, json=body).status_code == 200
    assert c.post(url, headers=headers, json=body).status_code == 200
    assert c.post(url, headers=headers, json={'output_root':'/etc/passwd'}).status_code == 400
    body['output_root'] = f'/node/share/elsewhere/runs/{task["execution_id"]}/output'
    assert c.post(url, headers=headers, json=body).status_code == 409
    with main.SessionLocal() as s:
        s.add(EpisodeRecord(run_id=task['run_id'], episode_id=2, seed=1, started={}, result={'artifacts':['result.json']}))
        s.commit()
    files = c.get(route(shared), headers=auth['alice']).json()['items']
    assert next(f for f in files if f['name']=='result.json')['episode_ids'] == [2]
    # Same bytes, multi-block transport; no whole-file byte column involved.
    payload = b'large-file\n' * 200000
    (output/'large.txt').write_bytes(payload)
    assert c.get(grant(shared,'large.txt')).content == payload


@pytest.mark.parametrize('value', ['{}','[{}]','null','[1]','broken'])
def test_invalid_config(monkeypatch, value):
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE', value)
    with pytest.raises(HTTPException) as error:shared_storage.mappings()
    assert error.value.status_code == 503


def test_component_mapping_and_ambiguity(shared, monkeypatch):
    mapping = shared[-1]
    with pytest.raises(HTTPException):shared_storage.match(mapping['node_names'][0], '/node/share-other/file')
    with pytest.raises(HTTPException):shared_storage.match(mapping['node_names'][0], '/node/share')
    with pytest.raises(HTTPException):shared_storage.match(mapping['node_names'][0], '/node/share/../other')
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE',json.dumps([mapping,{**mapping,'id':'second'}]))
    with pytest.raises(HTTPException):shared_storage.match(mapping['node_names'][0], '/node/share/file')


def test_io_admission_and_failure_mapping(shared, monkeypatch):
    mapping=shared[-1]
    first=shared_file_io.reserve('u');second=shared_file_io.reserve('u')
    with pytest.raises(HTTPException) as error:shared_file_io.reserve('u')
    assert error.value.status_code==429
    first();second()
    async def bad(*args,**kwargs):raise OSError()
    monkeypatch.setattr(asyncio,'create_subprocess_exec',bad)
    with pytest.raises(HTTPException) as error:asyncio.run(shared_file_io.read(mapping,'path',{},'u'))
    assert error.value.status_code==503 and shared_file_io._active=={}


def test_worker_shared_upload_does_not_read_file_content(tmp_path):
    from worker.client import Worker
    calls=[]
    worker=object.__new__(Worker)
    worker.workspace=tmp_path
    worker.api=SimpleNamespace(post=lambda path,body:calls.append((path,body)))
    task={'execution_id':str(uuid.uuid4()),'artifact_transport':'shared'}
    worker.upload_artifacts(task)
    assert len(calls)==1 and calls[0][0].endswith('/shared-artifacts')
    def fail(*args):raise RuntimeError('offline')
    worker.api=SimpleNamespace(post=fail)
    worker.upload_artifacts(task)  # Metadata failure never changes business outcome.


@pytest.mark.parametrize('status,code', [('missing',410),('permission_denied',403),('file_changed',409),
    ('root_changed',409),('unsafe_path',403),('unsupported_file',403),('invalid_path',400),
    ('invalid_request',400),('io_error',503),('storage_unavailable',503)])
def test_helper_error_contract(monkeypatch, status, code):
    class Process:
        returncode=0
        async def communicate(self, body):return json.dumps({'status':status}).encode(), b''
    async def spawn(*args,**kwargs):return Process()
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    with pytest.raises(HTTPException) as error:asyncio.run(shared_file_io.read({},'x',{},'u'))
    assert error.value.status_code==code and not shared_file_io._active


@pytest.mark.parametrize('mode', ['timeout','cancel','invalid','list','exit','oversize','request'])
def test_helper_lifecycle(monkeypatch, mode):
    class Process:
        returncode=None if mode in ('timeout','cancel') else (1 if mode=='exit' else 0)
        killed=False
        async def communicate(self, body):
            if mode=='timeout':raise asyncio.TimeoutError()
            if mode=='cancel':raise asyncio.CancelledError()
            return (b'[]' if mode=='list' else b'x'*(2097153 if mode=='oversize' else 1)),b''
        def kill(self):self.killed=True
        async def wait(self):self.returncode=-9
    process=Process()
    async def spawn(*args,**kwargs):return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    async def run():
        with pytest.raises(asyncio.CancelledError if mode=='cancel' else HTTPException) as error:
            await shared_file_io.read({},'x',{'large':'x'*33000} if mode=='request' else {},'u')
        if mode!='cancel':assert error.value.status_code==({'timeout':504,'request':400}.get(mode,503))
        await asyncio.sleep(0);await asyncio.sleep(0)
        assert not shared_file_io._active
    asyncio.run(run())
    if mode in ('timeout','cancel'):assert process.killed


def test_stream_global_budget_and_disconnect(monkeypatch):
    from backend.app.shared_artifacts import BoundedStream
    releases=[shared_file_io.reserve(str(n),streams=True) for n in range(8)]
    try:
        with pytest.raises(HTTPException):shared_file_io.reserve('ninth',streams=True)
    finally:
        for release in releases:release()
    async def broken(self,*args):raise asyncio.CancelledError()
    monkeypatch.setattr('starlette.responses.StreamingResponse.__call__',broken)
    response=BoundedStream(iter([b'x']),release=shared_file_io.reserve('u',streams=True))
    with pytest.raises(asyncio.CancelledError):asyncio.run(response({},None,None))
    assert not shared_file_io._streams


def test_filesystem_boundary_errors(tmp_path, monkeypatch):
    root=tmp_path/'share';root.mkdir()
    mapping={'api_root':str(root),'marker':'test-shared-store-001'}
    marker=root/'.mini-eval-storage-id';marker.write_text(mapping['marker'])
    assert shared_file_io.filesystem(mapping,'missing',{'operation':'list'})['status']=='missing'
    assert shared_file_io.filesystem(mapping,'../escape',{'operation':'list'})['status']=='unsafe_path'
    original=shared_file_io.reader.directory
    def denied(path, identity, relative=''):
        if relative:raise PermissionError()
        return original(path,identity,relative)
    monkeypatch.setattr(shared_file_io.reader,'directory',denied)
    assert shared_file_io.filesystem(mapping,'no-access',{'operation':'list'})['status']=='permission_denied'
    marker.unlink()
    assert shared_file_io.filesystem(mapping,'x',{})['status']=='storage_unavailable'
    root.rmdir()
    assert shared_file_io.filesystem(mapping,'x',{})['status']=='storage_unavailable'


def test_db_released_before_io_and_pagination(shared, monkeypatch):
    main,c,auth,_,_,task,output,_=shared
    original=shared_file_io.read
    async def checked(*args,**kwargs):
        assert main.SessionLocal.kw['bind'].pool.checkedout()==0
        return await original(*args,**kwargs)
    monkeypatch.setattr(shared_file_io,'read',checked)
    for index in range(105):(output/f'item{index:03}.txt').write_text('x')
    page=c.get(route(shared),headers=auth['alice']).json()
    assert len(page['items'])==100 and page['next']
    nextpage=c.get(route(shared),headers=auth['alice'],params={'after':page['next'],'version':page['version']})
    assert nextpage.status_code==200 and len(nextpage.json()['items'])==9
    (output/'new.txt').write_text('new')
    assert c.get(route(shared),headers=auth['alice'],params={'after':page['next'],'version':page['version']}).status_code==409
    for item in output.glob('item*.txt'):item.unlink()
    assert c.get(grant(shared,'video.mp4')).content==b'0123456789'
    assert not shared_file_io._streams


def test_claim_negotiates_shared_for_configured_node(env, monkeypatch):
    monkeypatch.setenv('MINI_EVAL_SHARED_STORAGE',json.dumps([dict(id='s',node_names=['node'],
        node_root='/shared',api_root='/mnt/shared',marker='test-shared-store-001')]))
    *_,task=prepare(env)
    assert task['artifact_transport']=='shared'


def test_filesystem_real_success_and_bad_marker(tmp_path):
    root=tmp_path/'share';root.mkdir();(root/'output').mkdir()
    mapping=dict(api_root=str(root),marker='test-shared-store-001')
    marker=root/'.mini-eval-storage-id';marker.write_text('wrong-marker')
    assert shared_file_io.filesystem(mapping,'output',{'operation':'list'})['status']=='storage_unavailable'
    marker.write_text(mapping['marker'])
    (root/'output'/'result.json').write_text('{"ok":true}')
    result=shared_file_io.filesystem(mapping,'output',{'operation':'list'})
    assert result['status']=='ready' and result['items'][0]['name']=='result.json'


def test_scope_and_offline_node(shared):
    from backend.app.core import Worker, now
    from datetime import timedelta
    from backend.tests.test_execution import node
    main,c,auth,_,_,task,_,_=shared
    other,_=node((main,c,auth),'another-node')
    url=f'/api/v1/worker/executions/{task["execution_id"]}/shared-artifacts'
    assert c.post(url,headers=other,json={'output_root':f'/node/share/runs/{task["execution_id"]}/output'}).status_code==404
    with main.SessionLocal() as s:
        slot=s.get(Slot,task['slot_id']) if 'slot_id' in task else s.scalar(select(Slot).where(Slot.node_name=='node'))
        worker=s.get(Worker,slot.worker_id);worker.enabled=False
        slot.heartbeat_at=slot.last_seen_at=now()-timedelta(days=3);s.commit()
    assert c.get(route(shared),headers=auth['alice']).status_code==200
