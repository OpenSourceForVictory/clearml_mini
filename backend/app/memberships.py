"""Pool membership intent, serialized with scheduling and execution cleanup."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, or_
from .core import Slot, Pool, Worker, Run, Job, MembershipAudit, now
from .scheduling import lock, online, transition, utc, ACTIVE
from . import storage, image_runtime


class Member(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slot_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=0)


class MembershipUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    members: list[Member] = Field(min_length=1, max_length=256)
    pool_id: str | None = Field(max_length=36)


def target_error(s, slot, target):
    if target is None:
        return None
    pool = s.get(Pool, target)
    if not pool or not pool.enabled:
        return 'target pool is missing or disabled'
    if (pool.gpu_model == 'virtual') != (slot.gpu_uuid or '').startswith('mock:'):
        return 'virtual and physical devices require different pool types'
    if slot.vram_mb < pool.min_vram_gb * 1024:
        return 'device has insufficient VRAM for target pool'
    return None


def ready(slot):
    return slot.pool_id is not None and slot.membership_revision == slot.membership_applied_revision


def reconcile(s):
    s.flush()
    for slot in s.scalars(select(Slot).where(Slot.membership_revision != Slot.membership_applied_revision)):
        if storage.held(s, slot) or target_error(s, slot, slot.desired_pool_id):
            continue
        slot.pool_id = slot.desired_pool_id
        slot.membership_applied_revision = slot.membership_revision
    s.flush()


def view(s, slot):
    pending = slot.membership_revision != slot.membership_applied_revision
    error = target_error(s, slot, slot.desired_pool_id) if pending else None
    state = 'blocked' if error else 'draining' if pending else 'assigned' if slot.pool_id else 'unassigned'
    worker = s.get(Worker, slot.worker_id) if slot.worker_id else None
    reasons = []
    if not online(slot):
        reasons.append('offline')
    if not worker or not worker.enabled or not slot.enabled:
        reasons.append('disabled')
    if state != 'assigned':
        reasons.append(state)
    elif target_error(s, slot, slot.pool_id):
        reasons.append('pool_unavailable')
    if slot.quarantined or s.scalar(select(Run.id).where(Run.slot_id == slot.id,
            Run.cleanup_confirmed.is_(False), Run.status.not_in(ACTIVE)).limit(1)):
        reasons.append('cleanup_pending')
    if worker and not storage.ready(worker, slot, now()):
        reasons.append('storage_pending')
    if worker and not image_runtime.ready(worker, slot, now()):
        reasons.append('runtime_pending')
    if slot.disk_min_free_mb is not None:
        if slot.disk_at is None or not 0 <= (now()-utc(slot.disk_at)).total_seconds() < 15:
            reasons.append('disk_unknown')
        elif slot.disk_free_mb is None or slot.disk_free_mb < slot.disk_min_free_mb:
            reasons.append('disk_full')
    return dict(desired_pool_id=slot.desired_pool_id, membership_revision=slot.membership_revision,
                membership_applied_revision=slot.membership_applied_revision,
                membership_updated_at=slot.membership_updated_at, membership_state=state,
                membership_error=error, admission_reasons=reasons, basic_ready=not reasons)


def summary(s, pool):
    slots = list(s.scalars(select(Slot).where(Slot.pool_id == pool.id)))
    models = {}
    for slot in slots:
        model = models.setdefault(slot.gpu_model or 'unknown', dict(count=0, vram_mb=0))
        model['count'] += 1
        model['vram_mb'] += slot.vram_mb
    jobs = list(s.scalars(select(Job.status).where(Job.pool_id == pool.id)))
    return dict(member_count=len(slots), online_count=sum(online(x) for x in slots),
        ready_count=sum(view(s, x)['basic_ready'] for x in slots), models=models,
        incoming_count=len(list(s.scalars(select(Slot.id).where(Slot.desired_pool_id == pool.id,
            or_(Slot.pool_id.is_(None), Slot.pool_id != pool.id))))),
        pending_jobs=jobs.count('PENDING'), active_jobs=sum(x in ACTIVE for x in jobs))


def install(app, db, admin):
    @app.put('/api/v1/admin/resource-memberships')
    def update(body: MembershipUpdate, user=Depends(admin), s=Depends(db)):
        lock(s)
        if len({x.slot_id for x in body.members}) != len(body.members):
            raise HTTPException(400, 'duplicate slot IDs')
        errors, slots = [], []
        for member in body.members:
            slot = s.get(Slot, member.slot_id)
            error = ('slot not found' if not slot else
                     'membership changed; refresh selection' if slot.membership_revision != member.expected_revision
                     else target_error(s, slot, body.pool_id))
            if error:
                errors.append({'slot_id': member.slot_id, 'reason': error})
            slots.append(slot)
        if errors:
            raise HTTPException(409, errors)
        for slot in slots:
            slot.membership_revision += 1
            slot.desired_pool_id = body.pool_id
            slot.membership_updated_at = now()
            s.add(MembershipAudit(actor_id=user.id, slot_id=slot.id, old_pool_id=slot.pool_id,
                                  target_pool_id=body.pool_id, revision=slot.membership_revision))
            for run in s.scalars(select(Run).where(Run.slot_id == slot.id, Run.status == 'RESERVED')):
                transition(s, run, 'PENDING', 'pool membership changed before claim')
                run.slot_id = run.execution_id = run.queue_name = run.reservation_expires_at = run.reserved_at = None
                run.reserved_vram_mb = run.reserved_cpu = run.reserved_ram_gb = 0
                run.allocation_index = 0
        reconcile(s)
        s.commit()
        return [{'slot_id': x.id, 'pool_id': x.pool_id, **view(s, x)} for x in slots]
