# ruff: noqa: F811
import hashlib
import time

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.artifact_media import byte_range, content_response
from backend.app.core import RunArtifact, User
from backend.tests.test_cs_protocol import prepare
from backend.tests.test_execution import env  # noqa: F401


@pytest.mark.parametrize('value,size,result', [
    (None, 10, (0, 9, False)), (None, 0, (0, -1, False)),
    ('bytes=0-0', 10, (0, 0, True)), ('bytes=3-6', 10, (3, 6, True)),
    ('bytes=7-', 10, (7, 9, True)), ('bytes=-3', 10, (7, 9, True)),
    ('bytes=-99', 10, (0, 9, True)), ('bytes=0-99', 10, (0, 9, True)),
])
def test_ranges(value, size, result):
    assert byte_range(value, size) == result


@pytest.mark.parametrize('value', ['bytes=10-', 'bytes=8-4', 'bytes=-0', 'bytes=-',
    'bytes=1-2,4-5', 'other=0-1', 'bytes=+1-2', 'bytes=１-2', 'bytes=1-2-3', 'bytes= 1-2', 'bytes=1-foo'])
def test_invalid_ranges(value):
    with pytest.raises(HTTPException) as error:
        byte_range(value, 10)
    assert error.value.status_code == 416
    assert error.value.headers == {'Content-Range': 'bytes */10'}


def test_empty_range():
    with pytest.raises(HTTPException):
        byte_range('bytes=0-', 0)


@pytest.mark.parametrize('method,headers,status,length', [
    ('GET', [(b'range', b'bytes=2-4')], 206, '3'),
    ('HEAD', [(b'range', b'bytes=2-4')], 200, '10'),
    ('GET', [(b'range', b'bytes=2-4'), (b'if-range', b'"changed"')], 200, '10'),
])
def test_response(method, headers, status, length):
    request = Request({'type': 'http', 'method': method, 'headers': headers})
    response = content_response(request, b'0123456789', 'movie.mp4', 'hash')
    assert response.status_code == status
    assert response.headers['content-length'] == length
    assert response.headers['content-type'] == 'video/mp4'
    assert response.body == (b'' if method == 'HEAD' else b'234' if status == 206 else b'0123456789')


def test_active_content_is_download_only():
    request = Request({'type': 'http', 'method': 'GET', 'headers': []})
    response = content_response(request, b'<svg/>', 'x\r\n.svg', 'hash')
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert '\r' not in response.headers['content-disposition']
    assert response.headers['x-content-type-options'] == 'nosniff'


def create_artifact(env):
    main, client, auth = env
    _, _, _, job, task = prepare(env)
    with main.SessionLocal() as s:
        item = RunArtifact(run_id=task['run_id'], name='episode-1/movie.mp4',
                           size_bytes=10, data=b'0123456789', sha256=hashlib.sha256(b'0123456789').hexdigest())
        s.add(item)
        s.commit()
        identity = item.id
    return client, auth, f'/api/v1/jobs/{job}/attempts/{task["run_id"]}/artifacts/{identity}', job, task, identity


def test_grant_and_range_permissions(env):
    client, auth, base, _, _, _ = create_artifact(env)
    assert client.get(base+'/media').status_code == 401
    assert client.post(base+'/media-access', headers=auth['bob']).status_code == 404
    response = client.post(base+'/media-access', headers=auth['alice'])
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert response.json()['url'] == base+'/media'
    assert '?' not in response.json()['url']
    response = client.get(base+'/media', headers={'Range': 'bytes=4-6'})
    assert response.status_code == 206
    assert response.content == b'456'
    assert response.headers['content-range'] == 'bytes 4-6/10'
    assert client.head(base+'/media').headers['content-length'] == '10'
    assert client.get(base+'/media', headers={'Range': 'bytes=99-'}).status_code == 416
    with env[0].SessionLocal() as s:
        s.get(User, 'alice').active = False
        s.commit()
    assert client.get(base+'/media').status_code == 401


def test_media_grant_scoped_expired_and_not_api_token(env):
    client, auth, base, job, task, identity = create_artifact(env)
    for expired, artifact in [(True, identity), (False, 'other')]:
        token = jwt.encode({'sub': 'alice', 'aud': 'artifact-media', 'job': job,
                            'run': task['run_id'], 'artifact': artifact,
                            'exp': time.time() - 10 if expired else time.time() + 60},
                           'test-only-secret', algorithm='HS256')
        assert client.get(base+'/media', headers={'Cookie': 'mini-eval-artifact='+token}).status_code == 401
        assert client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer '+token}).status_code == 401
    assert client.post(base.replace(identity, 'missing')+'/media-access', headers=auth['alice']).status_code == 404


def test_media_deleted_file_and_expiry_required(env):
    client, auth, base, _, _, identity = create_artifact(env)
    grant = client.post(base+'/media-access', headers=auth['alice'])
    assert grant.status_code == 200
    no_exp = jwt.encode({'sub': 'alice'}, 'test-only-secret', algorithm='HS256')
    assert client.post(base+'/media-access', headers={'Authorization': 'Bearer '+no_exp}).status_code == 401
    with env[0].SessionLocal() as s:
        s.delete(s.get(RunArtifact, identity))
        s.commit()
    assert client.get(base+'/media').status_code == 404


def test_declared_episode_artifact_references_only(env):
    from backend.app.core import EpisodeRecord
    client, auth, base, _, task, _ = create_artifact(env)
    main = env[0]
    with main.SessionLocal() as s:
        s.add(EpisodeRecord(run_id=task['run_id'], episode_id=2, seed=8,
                            started={'episode_id': 2}, result={'artifacts': ['episode-1/movie.mp4']}))
        s.commit()
    items = client.get(base.rsplit('/', 1)[0], headers=auth['alice']).json()
    assert items[0]['episode_ids'] == [2]  # Never infer episode 1 from its directory name.
    assert items[0]['media_type'] == 'video/mp4'


def test_webp_preview_and_json_download():
    request = Request({'type': 'http', 'method': 'GET', 'headers': []})
    response = content_response(request, b'RIFF', 'preview.WEBP', 'hash')
    assert response.headers['content-type'] == 'image/webp'
    assert response.headers['content-disposition'].startswith('inline;')
    response = content_response(request, b'{"html":"<script>"}', 'result.json', 'hash')
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
