"""Short-lived, file-scoped browser authorization and legacy artifact ranges."""
import os
import time
from urllib.parse import quote

import jwt
from fastapi import Depends, HTTPException, Request, Response

from .core import RunArtifact, User
from .execution_locations import visible_run

COOKIE = 'mini-eval-artifact'
TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.png': 'image/png',
         '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif'}


def media_type(name):
    suffix = os.path.splitext(name)[1].lower()
    return TYPES.get(suffix, 'application/octet-stream')


def byte_range(value, size):
    """One HTTP byte range, inclusive. Reject multipart and invalid requests."""
    if not value:
        return 0, size - 1, False
    try:
        if not value.startswith('bytes=') or ',' in value:
            raise ValueError()
        start, end = value[6:].split('-')
        if not start:
            if not end.isascii() or not end.isdecimal() or int(end) <= 0:
                raise ValueError()
            first, last = max(0, size - int(end)), size - 1
        else:
            if not start.isascii() or not start.isdecimal():
                raise ValueError()
            first = int(start)
            if end and (not end.isascii() or not end.isdecimal()):
                raise ValueError()
            last = min(int(end), size - 1) if end else size - 1
        if first < 0 or first >= size or last < first:
            raise ValueError()
        return first, last, True
    except (ValueError, OverflowError):
        raise HTTPException(416, 'invalid or unsatisfiable byte range',
                            headers={'Content-Range': f'bytes */{size}'}) from None


def content_response(request, content, name, digest):
    size = len(content)
    etag = '"' + digest + '"'
    requested = request.headers.get('range')
    if request.headers.get('if-range') not in (None, etag):
        requested = None
    # Range is only defined for GET; HEAD reports the complete representation.
    start, end, partial = byte_range(requested if request.method == 'GET' else None, size)
    kind = media_type(name)
    disposition = 'inline' if kind != 'application/octet-stream' else 'attachment'
    filename = quote(name.rsplit('/', 1)[-1], safe='')
    headers = {'Accept-Ranges': 'bytes', 'ETag': etag,
               'Content-Length': str(max(0, end - start + 1)),
               'Content-Disposition': f"{disposition}; filename*=UTF-8''{filename}",
               'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if partial:
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    return Response(b'' if request.method == 'HEAD' else content[start:end + 1],
                    status_code=206 if partial else 200, media_type=kind, headers=headers)


def install(app, db, current):
    root = '/api/v1/jobs/{job_id}/attempts/{run_id}/artifacts/{artifact_id}'

    @app.post(root + '/media-access')
    def grant(job_id: str, run_id: str, artifact_id: str, request: Request,
              response: Response, user=Depends(current), s=Depends(db)):
        visible_run(s, job_id, run_id, user)
        item = s.get(RunArtifact, artifact_id)
        if not item or item.run_id != run_id:
            raise HTTPException(404, 'artifact not found')
        secret = os.environ['JWT_SECRET']
        token = request.headers.get('authorization', '').partition(' ')[2]
        try:
            original = jwt.decode(token, secret, algorithms=['HS256'])
            expires = min(int(original['exp']), int(time.time()) + 300)
        except (jwt.PyJWTError, KeyError, ValueError, TypeError):
            raise HTTPException(401, 'invalid session') from None
        path = f'/api/v1/jobs/{job_id}/attempts/{run_id}/artifacts/{artifact_id}/media'
        grant_token = jwt.encode({'sub': user.id, 'aud': 'artifact-media', 'exp': expires,
                                  'job': job_id, 'run': run_id, 'artifact': artifact_id},
                                 secret, algorithm='HS256')
        response.set_cookie(COOKIE, grant_token, max_age=max(0, expires-int(time.time())),
                            httponly=True, secure=request.url.scheme == 'https', samesite='strict', path=path)
        return {'url': path, 'expires_at': expires, 'media_type': media_type(item.name)}

    @app.api_route(root + '/media', methods=['GET', 'HEAD'])
    def media(job_id: str, run_id: str, artifact_id: str, request: Request, s=Depends(db)):
        try:
            claims = jwt.decode(request.cookies.get(COOKIE, ''), os.environ['JWT_SECRET'],
                                algorithms=['HS256'], audience='artifact-media',
                                options={'require': ['exp', 'sub', 'aud']})
            if (claims.get('job'), claims.get('run'), claims.get('artifact')) != (job_id, run_id, artifact_id):
                raise ValueError()
        except (jwt.PyJWTError, ValueError):
            raise HTTPException(401, 'media authorization expired or invalid') from None
        user = s.get(User, claims['sub'])
        if not user or not user.active:
            raise HTTPException(401, 'invalid user')
        visible_run(s, job_id, run_id, user)
        item = s.get(RunArtifact, artifact_id)
        if not item or item.run_id != run_id:
            raise HTTPException(404, 'artifact not found')
        return content_response(request, item.data, item.name, item.sha256)
