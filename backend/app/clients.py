"""Client templates and immutable CS job snapshots."""
import json
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
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
    image: str = Field(default='', min_length=0, max_length=500, pattern=r'^([A-Za-z0-9][A-Za-z0-9_./:@-]*)?$')
    execution_mode: Literal['docker','host'] = 'docker'
    integration_mode: Literal['direct','sdk-command'] = 'direct'
    task_type: Literal['evaluation','command'] = 'evaluation'
    platform_bindings: list[dict] = Field(default_factory=list, max_length=32)
    parameter_schema: dict | None = None
    host_runtime: dict | None = None
    runtime: dict | None = None
    episode_reporting: bool = False
    endpoint_exclusive: bool = False
    command: list[str] = Field(min_length=1, max_length=64)
    protocols: list[Literal['http', 'https', 'ws', 'wss']] = Field(default=['http'], min_length=1, max_length=4)

    @model_validator(mode="after")
    def valid_runtime(self):
        from runner.template_contract import host_config
        from runner.task_input import validate_combined
        validate_combined(self.parameter_schema,self.platform_bindings)
        if self.task_type=='command':
            if self.episode_reporting or self.integration_mode!='direct':
                raise ValueError('ordinary commands do not use episode reporting')
        if (self.platform_bindings or self.task_type=='command') and self.execution_mode=='docker' and self.runtime is None:
            self.runtime={}
        if self.parameter_schema is not None:
            from runner.parameter_contract import validate_schema
            self.parameter_schema = validate_schema(self.parameter_schema)
            if self.execution_mode == 'docker' and self.runtime is None: self.runtime = {}
        if self.execution_mode == 'host':
            if self.image or self.runtime is not None:
                raise ValueError('host templates must not specify Docker image/runtime')
            self.host_runtime = host_config(self.host_runtime)
            for text in self.command: image_runtime.placeholders(text,self.host_runtime['assets'])
        elif not self.image or self.host_runtime is not None:
            raise ValueError('Docker templates require an image and no host runtime')
        if self.integration_mode == 'sdk-command':
            self.episode_reporting = True
            if self.execution_mode == 'docker' and self.runtime is None: self.runtime = {}
        if self.runtime is not None:
            self.runtime = image_runtime.validate(self.runtime, self.command)
        if self.parameter_schema is not None:
            settings = self.host_runtime if self.execution_mode == 'host' else self.runtime
            names = set(settings['environment']) | set(settings['overridable_env'])
            for field in self.parameter_schema['fields'].values():
                binding = field.get('binding', {})
                if binding.get('target') == 'env' and binding['name'] in names:
                    raise ValueError('business environment binding conflicts with runtime configuration')
        settings=self.host_runtime if self.execution_mode=='host' else self.runtime
        names=set((settings or {}).get('environment',{}))|set((settings or {}).get('overridable_env',[]))
        if any(b.get('target')=='env' and b.get('name') in names for b in self.platform_bindings):
            raise ValueError('platform binding conflicts with runtime configuration')
        return self

    @field_validator('command')
    @classmethod
    def valid_command(cls, value):
        if any(not x or len(x) > 2000 or '\x00' in x for x in value):
            raise ValueError('invalid command argument')
        return value


class TemplateState(BaseModel):
    enabled: bool


class Endpoint(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2000)
    mode: Literal['exclusive', 'shared']

    @field_validator('url')
    @classmethod
    def valid_url(cls, value):
        endpoint_parts(value)
        return value


class ClientRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template_id: str
    service_mode: Literal['external','none','managed'] = 'external'
    endpoint: str | None = Field(default=None, min_length=1, max_length=2000)
    endpoints: list[Endpoint] | None = Field(default=None, min_length=1, max_length=100)
    task_name: str = Field(min_length=1, max_length=200)
    seed: int = Field(default=0, ge=0, le=2147483647)
    request_timeout_seconds: int = Field(default=10, ge=1, le=120)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    parameters: dict = Field(default_factory=dict)
    parameter_overrides: dict = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("environment")
    @classmethod
    def valid_environment(cls, value):
        return image_runtime.environment(value)

    @model_validator(mode='after')
    def endpoint_selection(self):
        from .endpoints import resource_key
        if self.service_mode in ('none','managed'):
            if self.endpoint is not None or self.endpoints is not None:raise ValueError('automatic or absent service cannot include endpoints')
            return self
        if (self.endpoint is None) == (self.endpoints is None):
            raise ValueError('provide exactly one of endpoint or endpoints')
        if self.endpoints and len({resource_key(e.url) for e in self.endpoints}) != len(self.endpoints):
            raise ValueError('duplicate inference host and port in endpoints')
        return self

    @field_validator('endpoint')
    @classmethod
    def valid_endpoint(cls, value):
        if value is not None:
            endpoint_parts(value)
        return value

    @field_validator('parameters', 'parameter_overrides')
    @classmethod
    def bounded_parameters(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError('client parameters exceed 16 KiB')
        return value


def template_view(t):
    return {key: getattr(t, key) for key in ('id', 'name', 'version', 'image', 'command', 'protocols', 'enabled', 'episode_reporting', 'endpoint_exclusive', 'runtime', 'owner_id', 'execution_mode', 'integration_mode', 'host_runtime', 'archived', 'parameter_schema', 'task_type', 'platform_bindings','status','revision')}


def snapshot(s, body, owner_id=None):
    if body is None:
        raise HTTPException(400, 'CS evaluation requires client configuration')
    t = s.get(ClientTemplate, body.template_id)
    if not t or not t.enabled or t.archived or t.status!='published' or (t.owner_id is not None and t.owner_id != owner_id):
        raise HTTPException(400, 'unknown or disabled client template')
    if body.service_mode=='none' and t.task_type!='command':raise HTTPException(400,'evaluation templates require an inference service')
    urls = [e.url for e in body.endpoints] if body.endpoints else [body.endpoint] if body.endpoint else []
    if body.service_mode=='none' and any(b['source'].startswith('platform.service.') for b in t.platform_bindings):
        raise HTTPException(400,'template platform bindings require an inference service')
    if any(endpoint_parts(url)[0].scheme not in t.protocols for url in urls):
        raise HTTPException(400, 'endpoint protocol does not match client template')
    if body.timeout_seconds > seconds('EXECUTION_TIMEOUT_SECONDS', 3600):
        raise HTTPException(400, 'client timeout exceeds platform execution limit')
    parameters = body.parameters
    try:
        if t.parameter_schema is not None:
            from runner.parameter_contract import resolve_values
            parameters = resolve_values(t.parameter_schema, body.parameters, body.parameter_overrides)
        elif body.parameter_overrides:
            raise ValueError('parameter_overrides requires a typed template')
        if body.environment:
            runtime = t.host_runtime if t.execution_mode == 'host' else t.runtime
            if runtime is None or set(body.environment) - set(runtime['overridable_env']):
                raise ValueError('environment override is not allowed by this template')
            for value in body.environment.values():
                image_runtime.placeholders(value, runtime['assets'], runtime.get('mounts',{}))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'contract_version': VERSION, **body.model_dump(exclude={'template_id'}, exclude_none=True), 'parameters': parameters, 'template': template_view(t)}


def install(app, db, current):
    def admin(user=Depends(current)):
        if user.role != 'admin':
            raise HTTPException(403, 'admin required')
        return user

    @app.get('/api/v1/client-templates')
    def templates(user=Depends(current), s: Session = Depends(db)):
        q = select(ClientTemplate).where(ClientTemplate.archived.is_(False),ClientTemplate.status=='published').order_by(ClientTemplate.name, ClientTemplate.version)
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
        if body.execution_mode != 'docker':
            raise HTTPException(403, 'host templates require administrator approval')
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
        if not t or t.archived:
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
