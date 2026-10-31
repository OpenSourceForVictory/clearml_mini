from __future__ import annotations
import os
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
import jwt
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload
from .core import Event, Job, Pool, Run, RunLog, Slot, Status, User, SchedulerState, session_factory
from .scheduling import lock, loop, online, transition, TERMINAL, ACTIVE, utc
from .execution import install
from .clients import ClientRequest, snapshot, install as install_clients
from .batches import install as install_batches
from .episodes import install as install_episodes
from .builds import install as install_builds

SessionLocal = session_factory()
@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    thread = None
    if os.getenv('SCHEDULER_ENABLED', '1') == '1':
        thread = threading.Thread(target=loop, args=(SessionLocal, stop), daemon=True)
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=5)


app = FastAPI(title='Mini Eval Platform', lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


@app.exception_handler(RequestValidationError)
def validation_error(request, exc):
    if request.url.path in ('/api/v1/batches/preflight', '/api/v1/batches'):
        return JSONResponse(status_code=400, content={"detail": [
            {"reason": '.'.join(str(x) for x in err['loc'] if x != 'body') + ': ' + err['msg']}
            for err in exc.errors()]})
    return JSONResponse(status_code=400, content={"detail": "invalid request parameters"})
ph = PasswordHasher()
SECRET = os.getenv('JWT_SECRET')
if not SECRET:
    raise RuntimeError('JWT_SECRET is required')


def db():
    with SessionLocal() as s:
        yield s


def current(token: Optional[HTTPAuthorizationCredentials] = Depends(bearer), s: Session = Depends(db)):
    if token is None:
        raise HTTPException(401, "authentication required")
    try:
        uid = jwt.decode(token.credentials, SECRET, algorithms=['HS256'])['sub']
    except (jwt.PyJWTError, KeyError):
        raise HTTPException(401, 'invalid token')
    user = s.get(User, uid)
    if not user or not user.active:
        raise HTTPException(401, 'invalid user')
    return user


class Login(BaseModel):
    username: str
    password: str


class Submit(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    benchmark: Literal['mock', 'robotwin-render', 'cs-client']
    model_ref: str = Field(default='client', min_length=1, max_length=500)
    episodes: int = Field(ge=1, le=10000)
    pool_id: str
    gpu_mode: Literal['exclusive', 'shared'] = 'exclusive'
    requested_vram_gb: int = Field(default=0, ge=0, le=10000)
    required_gpu_model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    client: Optional[ClientRequest] = None


def visible(job: Job, user: User):
    if job.owner_id != user.id and user.role != 'admin':
        raise HTTPException(404, 'job not found')
    return job


def detail(job: Job):
    return {'id': job.id, 'owner_id': job.owner_id, 'owner_name': job.owner.username, 'title': job.title, 'benchmark': job.benchmark,
            'client_spec': job.client_spec, 'batch_id': job.batch_id, 'case_id': job.case_id,
            'model_ref': job.model_ref, 'episodes': job.episodes, 'pool_id': job.pool_id,
            'gpu_mode': job.gpu_mode, 'requested_vram_gb': job.requested_vram_gb,
            'required_gpu_model': job.required_gpu_model,
            'status': job.status, 'created_at': job.created_at, 'started_at': job.started_at,
            'finished_at': job.finished_at, 'error': job.error,
            'attempts': [{'id': r.id, 'attempt': r.attempt, 'status': r.status,
            'metrics': r.result_json, 'error': r.error, 'cleanup_confirmed': r.cleanup_confirmed, 'slot_id': r.slot_id, 'reserved_vram_mb': r.reserved_vram_mb,
            'log_bytes': r.log_bytes} for r in job.runs]}


@app.get('/healthz')
def health():
    return {'status': 'ok'}


@app.get('/readyz')
def ready(s: Session = Depends(db)):
    try:
        s.execute(select(User.id).limit(1))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={'status': 'degraded', 'database': 'unavailable'})
    return {'status': 'ready', 'database': 'ok'}


@app.post('/api/v1/auth/login')
def login(body: Login, s: Session = Depends(db)):
    user = s.scalar(select(User).where(User.username == body.username))
    try:
        valid = user and ph.verify(user.password_hash, body.password)
    except Exception:
        valid = False
    if not valid or not user.active:
        raise HTTPException(401, 'invalid credentials')
    token = jwt.encode({'sub': user.id, 'exp': datetime.now(timezone.utc) + timedelta(minutes=30)}, SECRET, algorithm='HS256')
    return {'access_token': token, 'token_type': 'bearer'}


@app.get('/api/v1/auth/me')
def me(user: User = Depends(current)):
    return {'id': user.id, 'username': user.username, 'role': user.role}


@app.get('/api/v1/pools')
def pools(user: User = Depends(current), s: Session = Depends(db)):
    from .memberships import summary
    return [{'id': p.id, 'name': p.name, 'gpu_model': p.gpu_model, 'min_vram_gb': p.min_vram_gb,
             'allow_shared': p.allow_shared, 'max_tasks_per_gpu': p.max_tasks_per_gpu,
             'vram_reserve_gb': p.vram_reserve_gb, 'max_gpu_utilization': p.max_gpu_utilization, **summary(s, p)}
            for p in s.scalars(select(Pool).where(Pool.enabled))]


@app.get('/api/v1/workers')
def workers(user: User = Depends(current), s: Session = Depends(db)):
    from .memberships import view
    slots = s.scalars(select(Slot)).all()
    if user.role == 'admin':
        held = s.scalars(select(Run).where(or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False)))).all()
        return [{'id': x.id, 'worker_id': x.worker_id, **view(s, x), 'node_name': x.node_name, 'gpu_index': x.gpu_index,
                 'gpu_uuid': x.gpu_uuid, 'pool_id': x.pool_id, 'online': online(x),
                 'quarantined': x.quarantined, 'last_seen_at': x.last_seen_at,
                 'gpu_model': x.gpu_model, 'vram_mb': x.vram_mb, 'free_vram_mb': x.free_vram_mb,
                 'gpu_utilization': x.gpu_utilization, 'telemetry_at': x.telemetry_at,
                 'disk_free_mb': x.disk_free_mb, 'disk_min_free_mb': x.disk_min_free_mb,
                 'disk_fresh': bool(x.disk_at and datetime.now(timezone.utc)-utc(x.disk_at) < timedelta(seconds=15)),
                 'telemetry_fresh': bool(x.telemetry_at and datetime.now(timezone.utc)-utc(x.telemetry_at) < timedelta(seconds=15)),
                 'protocol_version': x.protocol_version,
                 'active_tasks': sum(r.slot_id == x.id for r in held),
                 'reserved_vram_mb': sum(r.reserved_vram_mb for r in held if r.slot_id == x.id)} for x in slots]
    return {'total': len(slots), 'online': sum(online(x) for x in slots)}


@app.get('/api/v1/benchmarks')
def benchmarks(user: User = Depends(current)):
    values = [{'id': 'mock', 'name': '模拟评测'}, {'id': 'cs-client', 'name': 'CS 客户端评测'}]
    if os.getenv('ROBOTWIN_RENDER_ENABLED') == '1':
        values.append({'id': 'robotwin-render', 'name': 'RoboTwin 环境自检'})
    return values


@app.post('/api/v1/jobs', status_code=201)
def submit(body: Submit, idempotency_key: Optional[str] = Header(None), user: User = Depends(current), s: Session = Depends(db)):
    s.execute(select(User.id).where(User.id == user.id).with_for_update())
    pool = s.get(Pool, body.pool_id)
    if not pool or not pool.enabled:
        raise HTTPException(400, 'unknown or disabled pool')
    if body.gpu_mode == 'shared' and (not pool.allow_shared or body.requested_vram_gb < 1):
        raise HTTPException(400, 'shared jobs require an enabled pool and positive VRAM request')
    if pool.gpu_model == 'virtual' and (body.gpu_mode == 'shared' or body.requested_vram_gb):
        raise HTTPException(400, 'virtual pools do not allocate GPU memory')
    if body.benchmark == 'robotwin-render':
        if os.getenv('ROBOTWIN_RENDER_ENABLED') != '1':
            raise HTTPException(400, 'RoboTwin environment check is not enabled')
        if pool.gpu_model == 'virtual' or body.model_ref != 'sapien-render' or body.episodes > 100:
            raise HTTPException(400, 'environment check requires a GPU pool, model_ref=sapien-render and 1-100 episodes')
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
        raise HTTPException(400, 'invalid idempotency key')
    if idempotency_key:
        old = s.scalar(select(Job).where(Job.owner_id == user.id, Job.idempotency_key == idempotency_key))
        if old:
            return {'id': old.id, 'status': old.status}
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.status == 'PENDING'))
    if pending >= int(os.getenv('MAX_PENDING_PER_USER', '20')):
        raise HTTPException(429, 'pending limit')
    values = body.model_dump(exclude={'client'})
    if body.benchmark == 'cs-client':
        values['client_spec'] = snapshot(s, body.client, user.id)
        template = values['client_spec']['template']
        values['model_ref'] = template['name'] + ':' + template['version']
    elif body.client is not None:
        raise HTTPException(400, 'client configuration is only valid for CS evaluation')
    job = Job(owner_id=user.id, idempotency_key=idempotency_key, **values)
    s.add(job)
    s.flush()
    run = Run(job_id=job.id, attempt=1)
    s.add(run)
    s.flush()
    s.add(Event(job_id=job.id, run_id=run.id, new_status='PENDING'))
    s.commit()
    return {'id': job.id, 'status': job.status}


@app.get('/api/v1/jobs')
def jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: Optional[Status] = None, user: User = Depends(current), s: Session = Depends(db)):
    q = select(Job).options(selectinload(Job.runs), selectinload(Job.owner)).where(Job.owner_id == user.id).order_by(Job.created_at.desc())
    if status:
        q = q.where(Job.status == status)
    return [detail(j) for j in s.scalars(q.offset((page-1)*page_size).limit(page_size))]


@app.get('/api/v1/jobs/{job_id}')
def get_job(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    return detail(visible(job, user))


@app.post('/api/v1/jobs/{job_id}/cancel', status_code=202)
def cancel(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    lock(s)
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    visible(job, user)
    if job.status in ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'):
        raise HTTPException(409, 'terminal job')
    if job.status != 'CANCEL_REQUESTED':
        transition(s, job.runs[-1], 'CANCELLED' if job.status in ('PENDING', 'RESERVED') else 'CANCEL_REQUESTED')
    s.commit()
    return {'status': job.status}


@app.post('/api/v1/jobs/{job_id}/retry', status_code=202)
def retry(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    lock(s)
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    visible(job, user)
    if job.status not in ('FAILED','CANCELLED','TIMED_OUT'):
        raise HTTPException(409, 'job not retryable')
    if not job.runs[-1].cleanup_confirmed:
        raise HTTPException(409, 'previous execution cleanup is not confirmed')
    s.execute(select(User.id).where(User.id == job.owner_id).with_for_update())
    condition = Job.status.not_in(TERMINAL) if job.batch_id else Job.status == 'PENDING'
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == job.owner_id, condition))
    limit = int(os.getenv('MAX_BATCH_BACKLOG_PER_USER', '10000')) if job.batch_id else int(os.getenv('MAX_PENDING_PER_USER', '20'))
    if pending >= limit:
        raise HTTPException(429, 'pending limit')
    old = job.status
    job.status = 'PENDING'
    job.started_at = job.finished_at = job.queued_at = job.error = None
    run = Run(job_id=job.id, attempt=job.runs[-1].attempt + 1)
    s.add(run)
    s.flush()
    s.add(Event(job_id=job.id, run_id=run.id, old_status=old, new_status='PENDING'))
    s.commit()
    return {'id': job.id, 'attempt': run.attempt, 'status': job.status}


@app.get('/api/v1/admin/jobs')
def admin_jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               status: Optional[Status] = None, user: User = Depends(current), s: Session = Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403, 'admin required')
    query = select(Job).options(selectinload(Job.runs), selectinload(Job.owner)).order_by(Job.created_at.desc(), Job.id)
    if status:
        query = query.where(Job.status == status)
    return [detail(j) for j in s.scalars(query.offset((page-1)*page_size).limit(page_size))]


@app.get('/api/v1/jobs/{job_id}/logs')
def job_logs(job_id: str, run_id: Optional[str] = None, after: int = Query(-1, ge=-1),
             limit: int = Query(20, ge=1, le=100), user: User = Depends(current), s: Session = Depends(db)):
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    visible(job, user)
    run = next((r for r in job.runs if r.id == run_id), None) if run_id else job.runs[-1]
    if not run:
        raise HTTPException(404, 'attempt not found')
    rows = s.scalars(select(RunLog).where(RunLog.run_id == run.id, RunLog.sequence > after).order_by(RunLog.sequence).limit(limit)).all()
    return {'run_id': run.id, 'chunks': [{'sequence': r.sequence, 'content': r.content} for r in rows],
            'next_after': rows[-1].sequence if rows else after, 'max_bytes': 262144}


@app.get('/api/v1/admin/health')
def admin_health(user: User = Depends(current), s: Session = Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403, 'admin required')
    state = s.get(SchedulerState, 'heartbeat')
    return {'scheduler': state.value if state else None,
            'runner_image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:dev'),
            'quarantined_slots': s.scalar(select(func.count()).select_from(Slot).where(Slot.quarantined))}


install(app, db, current)
install_clients(app, db, current)
install_batches(app, db, current)
install_episodes(app, db, current)
install_builds(app, db, current)

frontend = Path(os.getenv('FRONTEND_DIST', str(Path(__file__).resolve().parents[2] / 'frontend' / 'dist')))
if frontend.is_dir():
    app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
