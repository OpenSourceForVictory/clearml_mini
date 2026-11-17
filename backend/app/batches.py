"""Explicit evaluation manifests; each case is an independently scheduled Job."""
from . import endpoints
import os
from typing import Literal, Optional
from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from .clients import ClientRequest, snapshot
from .core import ClientTemplate, EvaluationSuite, EvaluationBatch, EvaluationOrchestration, Event, Job, Pool, Run, User
from .scheduling import ACTIVE, TERMINAL, lock
from .episodes import progress
from .job_resources import Request as ResourceRequest


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid')
    case_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    task_name: str = Field(min_length=1, max_length=200)
    episodes: int = Field(ge=1, le=10000)
    seed: int = Field(default=0, ge=0, le=2147483647)
    parameters: dict = Field(default_factory=dict)
    service_keys: list[str] = Field(default_factory=list,max_length=100)
    resources: ResourceRequest = Field(default_factory=ResourceRequest)

    @model_validator(mode='after')
    def valid_service_keys(self):
        import re
        if len(set(self.service_keys))!=len(self.service_keys) or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',x) for x in self.service_keys):
            raise ValueError('service keys must be unique logical names')
        return self


class BatchClient(ClientRequest):
    task_name: str = 'batch'


class BatchSubmit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    pool_id: str
    gpu_mode: Literal['exclusive', 'shared'] = 'exclusive'
    requested_vram_gb: int = Field(default=0, ge=0, le=10000)
    required_gpu_model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    client: BatchClient
    max_concurrent: int = Field(default=1, ge=1, le=100)
    suite_id: Optional[str] = None
    cases: list[Case] = Field(default_factory=list, max_length=100)
    resources: ResourceRequest = Field(default_factory=ResourceRequest)

    @model_validator(mode='after')
    def unique_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate case_id in manifest')
        return self


class SuiteCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    template_id: str = ''
    template_ids: list[str] | None = Field(default=None, min_length=1, max_length=32)
    cases: list[Case] = Field(min_length=1, max_length=100)
    client_defaults: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_cases(self):
        if self.template_ids is None:self.template_ids=[self.template_id]
        if not self.template_id:self.template_id=self.template_ids[0]
        if any(not x or len(x)>36 for x in self.template_ids) or len(set(self.template_ids))!=len(self.template_ids) or self.template_id not in self.template_ids:
            raise ValueError('select distinct available templates and a default in that list')
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate case_id in manifest')
        for c in self.cases:
            ClientRequest.bounded_parameters(c.parameters)
        ClientRequest.bounded_parameters(self.client_defaults)
        return self


def suite_view(suite):
    return {k: getattr(suite, k) for k in ('id', 'name', 'version', 'template_id', 'template_ids', 'cases', 'created_at', 'status', 'archived', 'revision', 'description', 'tags', 'source','client_defaults')}


def batch_view(s, batch, jobs=None, progresses=None):
    jobs = jobs if jobs is not None else s.scalars(select(Job).options(selectinload(Job.runs)).where(Job.batch_id == batch.id)
                     .order_by(Job.created_at, Job.id)).all()
    succeeded = sum(j.status == 'SUCCEEDED' for j in jobs)
    finished = sum(j.status in TERMINAL for j in jobs)
    if jobs and succeeded == len(jobs):
        status = 'SUCCEEDED'
    elif any(j.status in ACTIVE for j in jobs):
        status = 'RUNNING'
    elif finished < len(jobs):
        status = 'PENDING'
    elif any(j.status in ('FAILED', 'TIMED_OUT') for j in jobs):
        status = 'FAILED'
    else:
        status = 'CANCELLED'
    return {'orchestrated':s.get(EvaluationOrchestration,batch.id) is not None,'scheduling_paused': batch.scheduling_paused, 'control_revision': batch.control_revision, 'control_updated_at': batch.control_updated_at, 'id': batch.id, 'suite_id': batch.suite_id, 'max_concurrent': batch.max_concurrent, 'title': batch.title, 'owner_id': batch.owner_id, 'created_at': batch.created_at,
            'status': status, 'total': len(jobs), 'succeeded': succeeded, 'finished': finished,
            'total_episodes': sum(j.episodes for j in jobs if j.client_spec['template'].get('task_type')!='command'),
            'cases': [{'job_id': j.id, 'case_id': j.case_id, 'task_name': j.client_spec['task_name'],
                       'seed': j.client_spec['seed'], 'episodes': 0 if j.client_spec['template'].get('task_type')=='command' else j.episodes, 'status': j.status,
                       'task_type': j.client_spec['template'].get('task_type','evaluation'),
                       **endpoints.view(j.runs[-1], j.client_spec), 'attempt': j.runs[-1].attempt, 'run_id': j.runs[-1].id,
                       'episode_reporting': j.client_spec['template'].get('episode_reporting', False),
                       'progress': progresses[j.runs[-1].id] if progresses is not None else progress(s, j.runs[-1]), 'error': j.error} for j in jobs]}


def resolve_batch(s, body, user, initial_defaults=None):
    """Resolve and validate without mutating the request or persisting records."""
    cases = list(body.cases)
    defaults=initial_defaults or {}
    if body.suite_id:
        suite = s.get(EvaluationSuite, body.suite_id)
        if not suite or suite.owner_id != user.id:
            raise HTTPException(404, 'suite not found')
        if suite.status != 'published' or suite.archived:
            raise HTTPException(409, 'suite must be published and not archived')
        if body.cases or body.client.template_id not in suite.template_ids:
            raise HTTPException(400, 'suite run must use saved cases and client template')
        cases = [Case.model_validate({**c,'resources':{k:v for k,v in c.get('resources',{}).items() if v is not None}}) for c in suite.cases]
        defaults=suite.client_defaults
    if not cases:
        raise HTTPException(400, 'evaluation manifest is empty')
    pool = s.get(Pool, body.pool_id)
    if not pool or not pool.enabled:
        raise HTTPException(400, 'unknown or disabled pool')
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.status.not_in(TERMINAL)))
    if pending + len(cases) > int(os.getenv('MAX_BATCH_BACKLOG_PER_USER', '10000')):
        raise HTTPException(429, 'manifest exceeds remaining pending quota')
    # Validate the entire manifest before persisting any of it.
    configs = []
    for case in cases:
        values = body.client.model_dump()
        values.update(task_name=case.task_name, seed=case.seed,
                      parameters={**defaults,**values['parameters'], **case.parameters})
        try:
            config = ClientRequest.model_validate(values)
        except ValueError:
            raise HTTPException(400, f'cases.{case.case_id}: invalid case configuration')
        try: configs.append(snapshot(s, config, user.id))
        except HTTPException as exc:
            raise HTTPException(exc.status_code, f'case {case.case_id}: {exc.detail}') from exc
        if configs[-1]['template'].get('task_type')=='command' and case.episodes!=1:
            raise HTTPException(400,f'case {case.case_id}: ordinary commands require episodes=1; they do not report episodes')
        from .job_resources import resolve,explicit,submission
        resource,sources=resolve(pool,configs[-1]['template'],('submission',submission(body)),('case',explicit(case.resources)))
        configs[-1]['resources']=resource
        configs[-1]['resource_sources']=sources
        configs[-1]['service_keys']=case.service_keys
        configs[-1]['case_id']=case.case_id
        schema=configs[-1]['template'].get('parameter_schema')
        if schema is not None:
            from runner.parameter_contract import resolve_layers
            try:
                result,sources=resolve_layers(schema,[('suite',defaults),('batch',body.client.parameters),('case',case.parameters)],body.client.parameter_overrides)
            except ValueError as exc:raise HTTPException(400,f'case {case.case_id}: {exc}') from exc
            configs[-1]['parameters']=result;configs[-1]['parameter_sources']=sources
    return pool, cases, configs


def persist_batch(s, body, user, pool, cases, configs, idempotency_key=None):
    batch = EvaluationBatch(owner_id=user.id, title=body.title, idempotency_key=idempotency_key,
                            suite_id=body.suite_id, max_concurrent=body.max_concurrent)
    s.add(batch); s.flush()
    for case, spec in zip(cases, configs):
        j = Job(owner_id=user.id, pool_id=pool.id, title=f'{body.title[:95]} / {case.case_id}',
                benchmark='cs-client', model_ref=spec['template']['name']+':'+spec['template']['version'],
                episodes=case.episodes, client_spec=spec, batch_id=batch.id, case_id=case.case_id,
                gpu_mode=spec['resources']['gpu_mode'], requested_vram_gb=spec['resources']['requested_vram_gb'],
                required_gpu_model=spec['resources']['required_gpu_model'],requested_cpu=spec['resources'].get('cpus'),requested_ram_gb=spec['resources'].get('ram_gb'))
        s.add(j); s.flush()
        run = Run(job_id=j.id, attempt=1)
        s.add(run); s.flush()
        s.add(Event(job_id=j.id, run_id=run.id, new_status='PENDING', message='created from evaluation manifest'))
    return batch


def install(app, db, current):
    from . import orchestrations
    orchestrations.install(app,db,current)
    from . import suites as suite_management
    suite_management.install(app, db, current)
    from .monitoring import install as install_monitoring
    install_monitoring(app, db, current)
    from .reports import install as install_reports
    install_reports(app, db, current)
    from .batch_controls import install as install_controls
    install_controls(app, db, current)
    @app.get('/api/v1/suites')
    def suites(page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        q = select(EvaluationSuite).where(EvaluationSuite.owner_id == user.id, EvaluationSuite.status == 'published', EvaluationSuite.archived.is_(False)).order_by(EvaluationSuite.created_at.desc())
        return [suite_view(x) for x in s.scalars(q.offset((page-1)*100).limit(100))]

    @app.post('/api/v1/batches/preflight')
    def preflight(body: BatchSubmit, user=Depends(current), s: Session = Depends(db)):
        if body.client.service_mode=='managed':raise HTTPException(400,'managed services require orchestration submission')
        from .preflight import inspect_batch
        pool, cases, configs = resolve_batch(s, body, user)
        return inspect_batch(s, body, user, pool, cases, configs)

    @app.post('/api/v1/batches', status_code=201)
    def create(body: BatchSubmit, idempotency_key: Optional[str] = Header(None), user=Depends(current), s: Session = Depends(db)):
        if body.client.service_mode=='managed':raise HTTPException(400,'managed services require orchestration submission')
        lock(s)
        s.execute(select(User.id).where(User.id == user.id).with_for_update())
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
            raise HTTPException(400, 'invalid idempotency key')
        if idempotency_key:
            old = s.scalar(select(EvaluationBatch).where(EvaluationBatch.owner_id == user.id,
                                                        EvaluationBatch.idempotency_key == idempotency_key))
            if old:
                if old.deleted_at: raise HTTPException(409, 'batch has been deleted')
                return batch_view(s, old)
        pool, cases, configs = resolve_batch(s, body, user)
        batch = persist_batch(s,body,user,pool,cases,configs,idempotency_key)
        s.commit()
        return batch_view(s, batch)

    @app.get('/api/v1/batches')
    def batches(page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        q = select(EvaluationBatch).where(EvaluationBatch.owner_id == user.id, EvaluationBatch.deleted_at.is_(None)).order_by(EvaluationBatch.created_at.desc(), EvaluationBatch.id.desc())
        return [batch_view(s, b) for b in s.scalars(q.offset((page-1)*20).limit(20))]

    @app.delete('/api/v1/batches/{batch_id}')
    def delete_batch(batch_id: str, user=Depends(current), s: Session = Depends(db)):
        from .batch_deletion import remove
        result = remove(s, batch_id, user)
        s.commit()
        return result

    @app.get('/api/v1/batches/{batch_id}')
    def detail(batch_id: str, user=Depends(current), s: Session = Depends(db)):
        b = s.get(EvaluationBatch, batch_id)
        if not b or b.deleted_at or (b.owner_id != user.id and user.role != 'admin'):
            raise HTTPException(404, 'batch not found')
        return batch_view(s, b)
