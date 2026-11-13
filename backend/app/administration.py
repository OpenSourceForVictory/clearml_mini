"""Non-destructive machine, pool, and account lifecycle management."""
import secrets
from typing import Literal
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_, func
from .core import Worker, Slot, Pool, Run, Job, User, ManagementAudit, now
from .scheduling import lock, online, ACTIVE, transition
from .storage import held


def audit(s, user, action, target, details=None):
    s.add(ManagementAudit(actor_id=user.id, action=action, target_id=target, details=details or {}))


def node_view(s, w):
    slots = list(s.scalars(select(Slot).where(Slot.worker_id == w.id)))
    return {**{k: getattr(w, k) for k in ('id', 'node_name', 'enabled', 'max_concurrent', 'cpu_capacity', 'ram_gb_capacity', 'roles', 'management_revision', 'revoked_at', 'connection')},
            'status': 'unmanaged' if w.revoked_at else 'disabled' if not w.enabled else 'online' if any(online(x) for x in slots) else 'offline' if slots else 'waiting',
            'held_executions': s.scalar(select(func.count()).select_from(Run).where(Run.slot_id.in_([x.id for x in slots]), or_(Run.status.in_(ACTIVE), Run.cleanup_confirmed.is_(False)))), 'gpu_count': len(slots)}


def release_reservations(s, slots):
    for run in s.scalars(select(Run).where(Run.slot_id.in_([x.id for x in slots]), Run.status == 'RESERVED')):
        transition(s, run, 'PENDING', 'machine admission disabled')
        run.slot_id = run.execution_id = run.queue_name = run.reservation_expires_at = run.reserved_at = None
        run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = 0
        run.allocation_index = 0


def update_node(s, w, body, user):
    if body.expected_revision is not None and body.expected_revision != w.management_revision:
        raise HTTPException(409, 'machine configuration changed; refresh')
    slots = list(s.scalars(select(Slot).where(Slot.worker_id == w.id)))
    values = body.model_dump(exclude_none=True, exclude={'expected_revision'})
    if 'roles' in values:
        values['roles'] = sorted(set(values['roles']))
        if values['roles'] != sorted(w.roles) and any(held(s, x) for x in slots):
            raise HTTPException(409, 'role changes require execution cleanup')
    for k, v in values.items(): setattr(w, k, v)
    if not w.enabled or 'client' not in w.roles: release_reservations(s, slots)
    w.management_revision += 1
    audit(s, user, 'machine.update', w.id, values)
    s.commit()
    return node_view(s, w)


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)


class PoolUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    allow_shared: bool | None = None
    max_tasks_per_gpu: int | None = Field(default=None, ge=1, le=32)
    vram_reserve_gb: int | None = Field(default=None, ge=0, le=10000)
    max_gpu_utilization: int | None = Field(default=None, ge=1, le=100)
    min_vram_gb: int | None = Field(default=None, ge=0, le=10000)
    cpu_per_job: int | None = Field(default=None, ge=1, le=10000)
    ram_gb_per_job: int | None = Field(default=None, ge=1, le=100000)


def update_pool(s, identity, body, user):
    lock(s)
    p = s.get(Pool, identity)
    if not p or p.archived: raise HTTPException(404, 'pool not found')
    values = body.model_dump(exclude_none=True)
    if body.name and s.scalar(select(Pool.id).where(Pool.name == body.name, Pool.id != identity)):
        raise HTTPException(409, 'pool name already exists')
    for k, v in values.items(): setattr(p, k, v)
    audit(s, user, 'pool.update', identity, values)
    s.commit()
    return {'id': p.id, **values}


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool | None = None
    role: Literal['user', 'admin'] | None = None
    password: str | None = Field(default=None, min_length=12, max_length=200)


def install(app, db, admin):
    @app.post('/api/v1/admin/nodes/{worker_id}/unmanage')
    def unmanage(worker_id: str, body: Revision, user=Depends(admin), s=Depends(db)):
        lock(s)
        w = s.get(Worker, worker_id)
        if not w: raise HTTPException(404, 'node not found')
        if w.revoked_at: return node_view(s, w)
        if body.expected_revision != w.management_revision: raise HTTPException(409, 'machine configuration changed; refresh')
        slots = list(s.scalars(select(Slot).where(Slot.worker_id == w.id)))
        w.enabled = False
        release_reservations(s, slots)
        w.management_revision += 1
        if any(held(s, x) for x in slots):
            audit(s, user, 'machine.drain', w.id)
            s.commit()
            raise HTTPException(409, 'machine disabled; wait for execution cleanup before unmanaging')
        w.revoked_at = now()
        # Tombstone retains audit and FK history, but cannot authenticate again.
        w.token_hash = secrets.token_hex(32)
        for slot in slots:
            slot.enabled = False
            slot.pool_id = slot.desired_pool_id = None
            slot.membership_revision += 1
            slot.membership_applied_revision = slot.membership_revision
        audit(s, user, 'machine.unmanage', w.id)
        s.commit()
        return node_view(s, w)

    @app.get('/api/v1/admin/pools')
    def pool_list(user=Depends(admin), s=Depends(db)):
        from .memberships import summary
        keys = ('id', 'name', 'gpu_model', 'enabled', 'min_vram_gb', 'allow_shared', 'max_tasks_per_gpu', 'vram_reserve_gb', 'max_gpu_utilization', 'cpu_per_job', 'ram_gb_per_job')
        return [{**{k: getattr(p, k) for k in keys}, **summary(s, p)} for p in s.scalars(select(Pool).where(Pool.archived.is_(False)).order_by(Pool.name))]

    @app.delete('/api/v1/admin/pools/{pool_id}')
    def delete_pool(pool_id: str, user=Depends(admin), s=Depends(db)):
        lock(s)
        p = s.get(Pool, pool_id)
        if not p: raise HTTPException(404, 'pool not found')
        members = s.scalar(select(Slot.id).where(or_(Slot.pool_id == pool_id, Slot.desired_pool_id == pool_id)).limit(1))
        jobs = s.scalar(select(Job.id).where(Job.pool_id == pool_id, Job.status.in_([*ACTIVE, 'PENDING'])).limit(1))
        cleanup = s.scalar(select(Run.id).join(Job).where(Job.pool_id == pool_id, Run.cleanup_confirmed.is_(False)).limit(1))
        if members or jobs or cleanup: raise HTTPException(409, 'pool has members, pending jobs or held executions')
        p.enabled, p.archived = False, True
        audit(s, user, 'pool.archive', pool_id)
        s.commit()
        return {'archived': True}

    @app.get('/api/v1/admin/users')
    def users(user=Depends(admin), s=Depends(db)):
        return [{k: getattr(u, k) for k in ('id', 'username', 'role', 'active', 'created_at')} for u in s.scalars(select(User).order_by(User.username))]

    @app.patch('/api/v1/admin/users/{user_id}')
    def account(user_id: str, body: AccountUpdate, user=Depends(admin), s=Depends(db)):
        lock(s)
        target = s.get(User, user_id)
        if not target: raise HTTPException(404, 'account not found')
        if target.active and target.role == 'admin' and (body.active is False or body.role == 'user'):
            count = s.scalar(select(func.count()).select_from(User).where(User.active, User.role == 'admin'))
            if count <= 1: raise HTTPException(409, 'cannot remove the last active administrator')
        values = body.model_dump(exclude_none=True, exclude={'password'})
        for k, v in values.items(): setattr(target, k, v)
        if body.password: target.password_hash = PasswordHasher().hash(body.password)
        audit(s, user, 'account.update', user_id, {**values, 'password_reset': body.password is not None})
        s.commit()
        return {k: getattr(target, k) for k in ('id', 'username', 'role', 'active')}
