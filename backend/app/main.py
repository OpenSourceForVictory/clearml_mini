from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
import jwt
from argon2 import PasswordHasher
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from .core import Base, Event, Job, Pool, Run, Slot, User, session_factory

SessionLocal = session_factory()
app = FastAPI(title='ClearML Mini Eval')
bearer = HTTPBearer(auto_error=False)


@app.exception_handler(RequestValidationError)
def validation_error(request, exc):
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
    benchmark: Literal['mock']
    model_ref: str = Field(min_length=1, max_length=500)
    episodes: int = Field(ge=1, le=10000)
    pool_id: str


def visible(job: Job, user: User):
    if job.owner_id != user.id and user.role != 'admin':
        raise HTTPException(404, 'job not found')
    return job


def detail(job: Job):
    return {'id': job.id, 'owner_id': job.owner_id, 'title': job.title, 'benchmark': job.benchmark,
            'model_ref': job.model_ref, 'episodes': job.episodes, 'pool_id': job.pool_id,
            'status': job.status, 'attempts': [{'id': r.id, 'attempt': r.attempt, 'status': r.status,
            'queue_name': r.queue_name, 'error': r.error} for r in job.runs]}


@app.get('/healthz')
def health():
    return {'status': 'ok'}


@app.get('/readyz')
def ready(s: Session = Depends(db)):
    s.execute(select(User.id).limit(1))
    return {'database': 'ok', 'clearml': 'not_checked'}


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
    return [{'id': p.id, 'name': p.name, 'gpu_model': p.gpu_model, 'min_vram_gb': p.min_vram_gb}
            for p in s.scalars(select(Pool).where(Pool.enabled))]


@app.get('/api/v1/workers')
def workers(user: User = Depends(current), s: Session = Depends(db)):
    slots = s.scalars(select(Slot)).all()
    if user.role == 'admin':
        return [{'id': x.id, 'node_name': x.node_name, 'gpu_index': x.gpu_index,
                 'gpu_uuid': x.gpu_uuid, 'queue_name': x.queue_name, 'last_seen_at': x.last_seen_at} for x in slots]
    return {'total': len(slots), 'online': sum(bool(x.last_seen_at) for x in slots)}


@app.post('/api/v1/jobs', status_code=201)
def submit(body: Submit, idempotency_key: Optional[str] = Header(None), user: User = Depends(current), s: Session = Depends(db)):
    s.execute(select(User.id).where(User.id == user.id).with_for_update())
    pool = s.get(Pool, body.pool_id)
    if not pool or not pool.enabled:
        raise HTTPException(400, 'unknown or disabled pool')
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
        raise HTTPException(400, 'invalid idempotency key')
    if idempotency_key:
        old = s.scalar(select(Job).where(Job.owner_id == user.id, Job.idempotency_key == idempotency_key))
        if old:
            return {'id': old.id, 'status': old.status}
    pending = s.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user.id, Job.status == 'PENDING'))
    if pending >= int(os.getenv('MAX_PENDING_PER_USER', '20')):
        raise HTTPException(429, 'pending limit')
    job = Job(owner_id=user.id, idempotency_key=idempotency_key, **body.model_dump())
    s.add(job)
    s.flush()
    run = Run(job_id=job.id, attempt=1)
    s.add(run)
    s.flush()
    s.add(Event(job_id=job.id, run_id=run.id, new_status='PENDING'))
    s.commit()
    return {'id': job.id, 'status': job.status}


@app.get('/api/v1/jobs')
def jobs(page: int = 1, page_size: int = 20, status: Optional[str] = None, user: User = Depends(current), s: Session = Depends(db)):
    q = select(Job).options(selectinload(Job.runs)).where(Job.owner_id == user.id).order_by(Job.created_at.desc())
    if status:
        q = q.where(Job.status == status)
    return [detail(j) for j in s.scalars(q.offset((max(1,page)-1)*min(page_size,100)).limit(min(page_size,100)))]


@app.get('/api/v1/jobs/{job_id}')
def get_job(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    return detail(visible(job, user))


@app.post('/api/v1/jobs/{job_id}/cancel', status_code=202)
def cancel(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    visible(job, user)
    if job.status in ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'):
        raise HTTPException(409, 'terminal job')
    old = job.status
    job.status = 'CANCELLED' if old == 'PENDING' else 'CANCEL_REQUESTED'
    job.runs[-1].status = job.status
    s.add(Event(job_id=job.id, run_id=job.runs[-1].id, old_status=old, new_status=job.status))
    s.commit()
    return {'status': job.status}


@app.post('/api/v1/jobs/{job_id}/retry', status_code=202)
def retry(job_id: str, user: User = Depends(current), s: Session = Depends(db)):
    job = s.get(Job, job_id, options=[selectinload(Job.runs)])
    if not job:
        raise HTTPException(404, 'job not found')
    visible(job, user)
    if job.status not in ('FAILED','CANCELLED','TIMED_OUT'):
        raise HTTPException(409, 'job not retryable')
    old = job.status
    job.status = 'PENDING'
    run = Run(job_id=job.id, attempt=job.runs[-1].attempt + 1)
    s.add(run)
    s.flush()
    s.add(Event(job_id=job.id, run_id=run.id, old_status=old, new_status='PENDING'))
    s.commit()
    return {'id': job.id, 'attempt': run.attempt, 'status': job.status}


@app.get('/api/v1/admin/jobs')
def admin_jobs(user: User = Depends(current), s: Session = Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403, 'admin required')
    return [detail(j) for j in s.scalars(select(Job).options(selectinload(Job.runs)))]
