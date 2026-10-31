"""Client templates and immutable CS job snapshots."""
import json
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from .core import ClientTemplate, Job, Run, RunArtifact
from .scheduling import lock, seconds
from runner.cs_contract import VERSION, endpoint_parts
from runner import image_runtime
from pydantic import model_validator


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    image: str = Field(min_length=1, max_length=500, pattern=r'^[A-Za-z0-9][A-Za-z0-9_./:@-]*$')
    runtime: dict | None = None
    episode_reporting: bool = False
    endpoint_exclusive: bool = False
    command: list[str] = Field(min_length=1, max_length=64)
    protocols: list[Literal['http', 'https', 'ws', 'wss']] = Field(default=['http'], min_length=1, max_length=4)

    @model_validator(mode="after")
    def valid_runtime(self):
        if self.runtime is not None:
            self.runtime = image_runtime.validate(self.runtime, self.command)
        return self

    @field_validator('command')
    @classmethod
    def valid_command(cls, value):
        if any(not x or len(x) > 2000 or '\x00' in x for x in value):
            raise ValueError('invalid command argument')
        return value


class TemplateState(BaseModel):
    enabled: bool


class ClientRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template_id: str
    endpoint: str = Field(min_length=1, max_length=2000)
    task_name: str = Field(min_length=1, max_length=200)
    seed: int = Field(default=0, ge=0, le=2147483647)
    request_timeout_seconds: int = Field(default=10, ge=1, le=120)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    parameters: dict = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def valid_environment(cls, value):
        return image_runtime.environment(value)

    @field_validator('endpoint')
    @classmethod
    def valid_endpoint(cls, value):
        endpoint_parts(value)
        return value

    @field_validator('parameters')
    @classmethod
    def bounded_parameters(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError('client parameters exceed 16 KiB')
        return value


def template_view(t):
    return {key: getattr(t, key) for key in ('id', 'name', 'version', 'image', 'command', 'protocols', 'enabled', 'episode_reporting', 'endpoint_exclusive', 'runtime', 'owner_id')}


def snapshot(s, body, owner_id=None):
    if body is None:
        raise HTTPException(400, 'CS evaluation requires client configuration')
    t = s.get(ClientTemplate, body.template_id)
    if not t or not t.enabled or (t.owner_id is not None and t.owner_id != owner_id):
        raise HTTPException(400, 'unknown or disabled client template')
    parts, _ = endpoint_parts(body.endpoint)
    if parts.scheme not in t.protocols:
        raise HTTPException(400, 'endpoint protocol does not match client template')
    if body.timeout_seconds > seconds('EXECUTION_TIMEOUT_SECONDS', 3600):
        raise HTTPException(400, 'client timeout exceeds platform execution limit')
    try:
        if body.environment:
            if t.runtime is None or set(body.environment) - set(t.runtime['overridable_env']):
                raise ValueError('environment override is not allowed by this template')
            for value in body.environment.values():
                image_runtime.placeholders(value, t.runtime['assets'])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'contract_version': VERSION, **body.model_dump(exclude={'template_id'}), 'template': template_view(t)}


def install(app, db, current):
    def admin(user=Depends(current)):
        if user.role != 'admin':
            raise HTTPException(403, 'admin required')
        return user

    @app.get('/api/v1/client-templates')
    def templates(user=Depends(current), s: Session = Depends(db)):
        q = select(ClientTemplate).order_by(ClientTemplate.name, ClientTemplate.version)
        if user.role != 'admin':
            q = q.where(ClientTemplate.enabled, or_(ClientTemplate.owner_id.is_(None), ClientTemplate.owner_id == user.id))
        return [template_view(t) for t in s.scalars(q)]

    @app.post('/api/v1/admin/client-templates', status_code=201)
    def create(body: TemplateCreate, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        if s.scalar(select(ClientTemplate.id).where(ClientTemplate.name == body.name, ClientTemplate.version == body.version)):
            raise HTTPException(409, 'client template version already exists')
        t = ClientTemplate(**body.model_dump())
        s.add(t); s.commit()
        return template_view(t)

    @app.post('/api/v1/client-templates', status_code=201)
    def create_private(body: TemplateCreate, user=Depends(current), s: Session = Depends(db)):
        if body.runtime is None:
            raise HTTPException(400, 'user image templates require runtime configuration')
        lock(s)
        if s.scalar(select(ClientTemplate.id).where(ClientTemplate.name == body.name, ClientTemplate.version == body.version)):
            raise HTTPException(409, 'client template version already exists')
        t = ClientTemplate(owner_id=user.id, **body.model_dump())
        s.add(t); s.commit()
        return template_view(t)

    @app.patch('/api/v1/admin/client-templates/{template_id}')
    def enable(template_id: str, body: TemplateState, user=Depends(admin), s: Session = Depends(db)):
        lock(s)
        t = s.get(ClientTemplate, template_id)
        if not t:
            raise HTTPException(404, 'client template not found')
        t.enabled = body.enabled
        s.commit()
        return template_view(t)

    def visible_run(s, job_id, run_id, user):
        job, run = s.get(Job, job_id), s.get(Run, run_id)
        if not job or (job.owner_id != user.id and user.role != 'admin') or not run or run.job_id != job.id:
            raise HTTPException(404, 'attempt not found')
        return run

    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/artifacts')
    def artifacts(job_id: str, run_id: str, user=Depends(current), s: Session = Depends(db)):
        visible_run(s, job_id, run_id, user)
        return [{k: getattr(a, k) for k in ('id', 'name', 'size_bytes', 'sha256')}
                for a in s.scalars(select(RunArtifact).where(RunArtifact.run_id == run_id).order_by(RunArtifact.name))]

    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/artifacts/{artifact_id}')
    def download(job_id: str, run_id: str, artifact_id: str, user=Depends(current), s: Session = Depends(db)):
        visible_run(s, job_id, run_id, user)
        a = s.get(RunArtifact, artifact_id)
        if not a or a.run_id != run_id:
            raise HTTPException(404, 'artifact not found')
        return Response(content=a.data, media_type='application/octet-stream', headers={
            'Content-Disposition': f'attachment; filename="{a.name.split("/")[-1]}"',
            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
