"""Explicit evaluation manifests; each case is an independently scheduled Job."""
import os
from typing import Literal, Optional
from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from .clients import ClientRequest, snapshot
from .core import ClientTemplate, EvaluationSuite, EvaluationBatch, Event, Job, Pool, Run, User
from .scheduling import ACTIVE, TERMINAL, lock
from .episodes import progress


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid')
    case_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_.-]+$')
    task_name: str = Field(min_length=1, max_length=200)
    episodes: int = Field(ge=1, le=10000)
    seed: int = Field(default=0, ge=0, le=2147483647)
    parameters: dict = Field(default_factory=dict)


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

    @model_validator(mode='after')
    def unique_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate case_id in manifest')
        return self


class SuiteCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    template_id: str
    cases: list[Case] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError('duplicate case_id in manifest')
        for c in self.cases:
            ClientRequest.bounded_parameters(c.parameters)
        return self


def suite_view(suite):
    return {k: getattr(suite, k) for k in ('id', 'name', 'version', 'template_id', 'cases', 'created_at', 'status', 'archived', 'revision', 'description', 'tags', 'source')}


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
    return {'scheduling_paused': batch.scheduling_paused, 'control_revision': batch.control_revision, 'control_updated_at': batch.control_updated_at, 'id': batch.id, 'suite_id': batch.suite_id, 'max_concurrent': batch.max_concurrent, 'title': batch.title, 'owner_id': batch.owner_id, 'created_at': batch.created_at,
            'status': status, 'total': len(jobs), 'succeeded': succeeded, 'finished': finished,
            'total_episodes': sum(j.episodes for j in jobs),
            'cases': [{'job_id': j.id, 'case_id': j.case_id, 'task_name': j.client_spec['task_name'],
                       'seed': j.client_spec['seed'], 'episodes': j.episodes, 'status': j.status,
                       'attempt': j.runs[-1].attempt, 'run_id': j.runs[-1].id,
                       'episode_reporting': j.client_spec['template'].get('episode_reporting', False),
                       'progress': progresses[j.runs[-1].id] if progresses is not None else progress(s, j.runs[-1]), 'error': j.error} for j in jobs]}


def resolve_batch(s, body, user):
    """Resolve and validate without mutating the request or persisting records."""
    cases = list(body.cases)
    if body.suite_id:
        suite = s.get(EvaluationSuite, body.suite_id)
        if not suite or suite.owner_id != user.id:
            raise HTTPException(404, 'suite not found')
        if suite.status != 'published' or suite.archived:
            raise HTTPException(409, 'suite must be published and not archived')
        if body.cases or body.client.template_id != suite.template_id:
            raise HTTPException(400, 'suite run must use saved cases and client template')
        cases = [Case.model_validate(c) for c in suite.cases]
    if not cases:
        raise HTTPException(400, 'evaluation manifest is empty')
    pool = s.get(Pool, body.pool_id)
    if not pool or not pool.enabled:
        raise HTTPException(400, 'unknown or disabled pool')
    if body.gpu_mode == 'shared' and (not pool.allow_shared or body.requested_vram_gb < 1):
        raise HTTPException(400, 'shared jobs require an enabled pool and positive VRAM request')
    if pool.gpu_model == 'virtual' and (body.gpu_mode == 'shared' or body.requested_vram_gb):
        raise HTTPException(400, 'virtual pools do not allocate GPU memory')
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.status.not_in(TERMINAL)))
    if pending + len(cases) > int(os.getenv('MAX_BATCH_BACKLOG_PER_USER', '10000')):
        raise HTTPException(429, 'manifest exceeds remaining pending quota')
    # Validate the entire manifest before persisting any of it.
    configs = []
    for case in cases:
        values = body.client.model_dump()
        values.update(task_name=case.task_name, seed=case.seed,
                      parameters={**values['parameters'], **case.parameters})
        try:
            config = ClientRequest.model_validate(values)
        except ValueError:
            raise HTTPException(400, f'cases.{case.case_id}: invalid case configuration')
        configs.append(snapshot(s, config, user.id))
    return pool, cases, configs


def install(app, db, current):
    from . import suites as suite_management
    suite_management.install(app, db, current)
    from .monitoring import install as install_monitoring
    install_monitoring(app, db, current)
    from .batch_controls import install as install_controls
    install_controls(app, db, current)
    @app.get('/api/v1/suites')
    def suites(page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        q = select(EvaluationSuite).where(EvaluationSuite.owner_id == user.id, EvaluationSuite.status == 'published', EvaluationSuite.archived.is_(False)).order_by(EvaluationSuite.created_at.desc())
        return [suite_view(x) for x in s.scalars(q.offset((page-1)*100).limit(100))]

    @app.post('/api/v1/batches/preflight')
    def preflight(body: BatchSubmit, user=Depends(current), s: Session = Depends(db)):
        from .preflight import inspect_batch
        pool, cases, configs = resolve_batch(s, body, user)
        return inspect_batch(s, body, user, pool, cases, configs)

    @app.post('/api/v1/batches', status_code=201)
    def create(body: BatchSubmit, idempotency_key: Optional[str] = Header(None), user=Depends(current), s: Session = Depends(db)):
        lock(s)
        s.execute(select(User.id).where(User.id == user.id).with_for_update())
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
            raise HTTPException(400, 'invalid idempotency key')
        if idempotency_key:
            old = s.scalar(select(EvaluationBatch).where(EvaluationBatch.owner_id == user.id,
                                                        EvaluationBatch.idempotency_key == idempotency_key))
            if old:
                return batch_view(s, old)
        pool, cases, configs = resolve_batch(s, body, user)
        batch = EvaluationBatch(owner_id=user.id, title=body.title, idempotency_key=idempotency_key,
                                suite_id=body.suite_id, max_concurrent=body.max_concurrent)
        s.add(batch); s.flush()
        for case, spec in zip(cases, configs):
            j = Job(owner_id=user.id, pool_id=pool.id, title=f'{body.title[:95]} / {case.case_id}',
                    benchmark='cs-client', model_ref=spec['template']['name']+':'+spec['template']['version'],
                    episodes=case.episodes, client_spec=spec, batch_id=batch.id, case_id=case.case_id,
                    gpu_mode=body.gpu_mode, requested_vram_gb=body.requested_vram_gb,
                    required_gpu_model=body.required_gpu_model)
            s.add(j); s.flush()
            run = Run(job_id=j.id, attempt=1)
            s.add(run); s.flush()
            s.add(Event(job_id=j.id, run_id=run.id, new_status='PENDING', message='created from evaluation manifest'))
        s.commit()
        return batch_view(s, batch)

    @app.get('/api/v1/batches')
    def batches(page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        q = select(EvaluationBatch).where(EvaluationBatch.owner_id == user.id).order_by(EvaluationBatch.created_at.desc())
        return [batch_view(s, b) for b in s.scalars(q.offset((page-1)*20).limit(20))]

    @app.get('/api/v1/batches/{batch_id}')
    def detail(batch_id: str, user=Depends(current), s: Session = Depends(db)):
        b = s.get(EvaluationBatch, batch_id)
        if not b or (b.owner_id != user.id and user.role != 'admin'):
            raise HTTPException(404, 'batch not found')
        return batch_view(s, b)
