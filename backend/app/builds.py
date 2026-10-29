"""Bounded source submissions; a separately operated builder creates images."""
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Literal
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from .core import ClientBuild, ClientTemplate, now
from .scheduling import lock, utc
from runner.cs_contract import artifact_name


class SourceSubmit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    entrypoint: str = Field(max_length=200, pattern=r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$')
    files: dict[str, str]

    @model_validator(mode='after')
    def safe_files(self):
        if not 1 <= len(self.files) <= 100 or len(json.dumps(self.files).encode()) > 1048576:
            raise ValueError('source bundle must contain 1–100 files and at most 1 MiB')
        for path, content in self.files.items():
            if not artifact_name(path) or path.split('/')[0] in ('runner', 'Dockerfile', '.dockerignore') or '\x00' in content:
                raise ValueError('invalid or reserved source path')
            if not path.endswith(('.py', '.json', '.txt', '.yaml', '.yml', '.md')):
                raise ValueError('unsupported source file type')
        return self


class BuildResult(BaseModel):
    token: str
    status: Literal['SUCCEEDED', 'FAILED']
    image: str | None = Field(default=None, max_length=500, pattern=r'^[A-Za-z0-9][A-Za-z0-9_./:@-]*$')
    log: str = Field(max_length=65536)


def view(b):
    return {k: getattr(b, k) for k in ('id', 'name', 'version', 'entrypoint', 'sha256', 'status', 'log', 'image', 'template_id', 'created_at')}


def install(app, db, current):
    def admin(user=Depends(current)):
        if user.role != 'admin': raise HTTPException(403, 'admin required')
        return user

    def expire(s):
        for b in s.scalars(select(ClientBuild).where(ClientBuild.status == 'BUILDING', ClientBuild.expires_at < now())):
            b.status = 'FAILED'; b.log += '\nBuilder deadline expired; no client version was published.'

    @app.post('/api/v1/client-builds', status_code=201)
    def submit(body: SourceSubmit, user=Depends(current), s: Session = Depends(db)):
        lock(s); expire(s)
        if s.scalar(select(ClientBuild.id).where(ClientBuild.owner_id == user.id, ClientBuild.name == body.name, ClientBuild.version == body.version)):
            raise HTTPException(409, 'source version already exists')
        count = s.scalar(select(func.count()).select_from(ClientBuild).where(ClientBuild.owner_id == user.id, ClientBuild.status.in_(['PENDING', 'BUILDING'])))
        if count >= 5: raise HTTPException(429, 'client build backlog quota exceeded')
        source = {'files': body.files, 'entrypoint': body.entrypoint}
        digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        b = ClientBuild(owner_id=user.id, name=body.name, version=body.version, entrypoint=body.entrypoint, source=source, sha256=digest)
        s.add(b); s.commit(); return view(b)

    @app.get('/api/v1/client-builds')
    def builds(page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        lock(s); expire(s); s.commit()
        return [view(b) for b in s.scalars(select(ClientBuild).where(ClientBuild.owner_id == user.id)
                    .order_by(ClientBuild.created_at.desc()).offset((page-1)*20).limit(20))]

    @app.post('/api/v1/admin/client-builds/claim')
    def claim(user=Depends(admin), s: Session = Depends(db)):
        lock(s); expire(s)
        b = s.scalar(select(ClientBuild).where(ClientBuild.status == 'PENDING').order_by(ClientBuild.created_at, ClientBuild.id))
        if not b:
            s.commit(); return {'build': None}
        token = secrets.token_urlsafe(32)
        b.claim_hash = hashlib.sha256(token.encode()).hexdigest()
        b.expires_at = now()+timedelta(minutes=30); b.status = 'BUILDING'
        s.commit()
        return {'build': {**view(b), 'source': b.source, 'token': token}}

    @app.post('/api/v1/admin/client-builds/{build_id}/complete')
    def finish(build_id: str, body: BuildResult, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        b = s.get(ClientBuild, build_id)
        if not b or not secrets.compare_digest(b.claim_hash or '', hashlib.sha256(body.token.encode()).hexdigest()):
            raise HTTPException(404, 'build not found')
        if b.status in ('SUCCEEDED', 'FAILED'):
            if b.status == body.status and b.image == body.image and b.log == body.log: return view(b)
            raise HTTPException(409, 'build already ended')
        if utc(b.expires_at) < now():
            raise HTTPException(409, 'build lease expired')
        if body.status == 'SUCCEEDED':
            if not body.image: raise HTTPException(400, 'successful build requires image')
            t = ClientTemplate(owner_id=b.owner_id, name='source-'+b.id, version=b.version, image=body.image,
                command=['python', '-m', 'runner.sdk', b.entrypoint], protocols=['http', 'https', 'ws', 'wss'], episode_reporting=True)
            s.add(t); s.flush(); b.template_id = t.id
        b.status = body.status; b.image = body.image; b.log = body.log
        s.commit(); return view(b)
