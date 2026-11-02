"""Batch observability and explicit, attempt-guarded recovery."""
from . import endpoints
from collections import Counter, defaultdict
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from .core import EvaluationBatch, Job, User, Pool, EpisodeRecord
from .episodes import summarize
from .preflight import DiagnosticContext, reason
from .retries import retry_job
from .scheduling import lock


class RetryItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1, max_length=36)
    expected_run_id: str = Field(min_length=1, max_length=36)


class RetrySelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[RetryItem] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({x.job_id for x in self.items}) != len(self.items):
            raise ValueError('duplicate job selection')
        return self


SKIP = {
    'attempt changed': ('attempt_changed', '尝试已变化，请刷新后重新选择'),
    'job not retryable': ('not_retryable', '任务当前不是执行失败或超时状态'),
    'previous execution cleanup is not confirmed': ('cleanup_pending', '旧执行尚未确认清理，暂时不能重试'),
    'pending limit': ('backlog_limit', '剩余积压额度不足，请等待已有任务完成'),
}


def owned(s, identity, user):
    batch = s.get(EvaluationBatch, identity)
    if not batch or (batch.owner_id != user.id and user.role != 'admin'):
        raise HTTPException(404, 'batch not found')
    return batch


def batch_jobs(s, batch):
    return s.scalars(select(Job).options(selectinload(Job.runs)).where(Job.batch_id == batch.id)
                     .order_by(Job.created_at, Job.id)).all()


def monitor(s, batch):
    from .batches import batch_view
    jobs = batch_jobs(s, batch)
    records = defaultdict(list)
    for row in s.scalars(select(EpisodeRecord).where(EpisodeRecord.run_id.in_([j.runs[-1].id for j in jobs]))):
        records[row.run_id].append(row)
    progresses = {j.runs[-1].id: summarize(records[j.runs[-1].id]) for j in jobs}
    result = batch_view(s, batch, jobs, progresses)
    context = DiagnosticContext(s)
    owner = s.get(User, batch.owner_id)
    pools = {p.id: p for p in s.scalars(select(Pool).where(Pool.id.in_({j.pool_id for j in jobs})))}
    reporting = [c for c in result['cases'] if c['episode_reporting']]
    completed = sum(c['progress']['completed'] for c in reporting)
    successes = sum(c['progress']['successes'] for c in reporting)
    result.update(checked_at=context.at, summary=dict(states=dict(Counter(j.status for j in jobs)),
        cancel_requested_tasks=sum(j.status == 'CANCEL_REQUESTED' for j in jobs),
        cleanup_pending_tasks=sum(not j.runs[-1].cleanup_confirmed and j.status in ('CANCEL_REQUESTED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT') for j in jobs),
        completed_episodes=completed, successes=successes, goal_failures=completed-successes,
        episode_errors=sum(c['progress']['errors'] for c in reporting),
        execution_failures=sum(j.status in ('FAILED', 'TIMED_OUT') for j in jobs),
        reporting_tasks=len(reporting), unreported_tasks=len(jobs)-len(reporting),
        success_rate=successes/completed if completed else None))
    for case, job in zip(result['cases'], jobs):
        run = job.runs[-1]
        slot = context.slots.get(run.slot_id)
        case.update(cleanup_confirmed=run.cleanup_confirmed,
                    node_name=slot.node_name if slot else None, gpu_index=slot.gpu_index if slot else None,
                    waiting=None, reason_codes=[])
        if job.status == 'PENDING':
            waiting = context.inspect(job, owner, pools.get(job.pool_id), batch)
            if waiting['status'] == 'ready':
                waiting['status'] = 'awaiting_scheduler'
                waiting['reasons'].append(reason('awaiting_scheduler'))
            case['waiting'] = waiting
            case['reason_codes'] = sorted({r['code'] for r in waiting['reasons']} |
                {r['code'] for n in waiting['nodes'] for r in n['reasons']})
        spec = job.client_spec
        case['config'] = dict(pool_id=job.pool_id, pool_name=pools[job.pool_id].name if job.pool_id in pools else job.pool_id,
                              gpu_mode=job.gpu_mode, requested_vram_gb=job.requested_vram_gb,
                              required_gpu_model=job.required_gpu_model, **endpoints.view(run, spec),
                              client_name=spec['template']['name'], client_version=spec['template']['version'])
    return result


def install(app, db, current):
    @app.get('/api/v1/batches/{batch_id}/monitor')
    def get(batch_id: str, user=Depends(current), s=Depends(db)):
        return monitor(s, owned(s, batch_id, user))

    @app.post('/api/v1/batches/{batch_id}/retry-failed')
    def retry(batch_id: str, body: RetrySelection, user=Depends(current), s=Depends(db)):
        lock(s)
        batch = owned(s, batch_id, user)
        jobs = batch_jobs(s, batch)
        wanted = {item.job_id: item.expected_run_id for item in body.items}
        if set(wanted) - {j.id for j in jobs}:
            raise HTTPException(404, 'batch task not found')
        s.execute(select(User.id).where(User.id == batch.owner_id).with_for_update())
        items = []
        for job in jobs:
            if job.id not in wanted: continue
            try:
                run = retry_job(s, job, wanted[job.id], allowed=('FAILED', 'TIMED_OUT'))
                items.append(dict(job_id=job.id, outcome='retried', run_id=run.id, attempt=run.attempt))
            except HTTPException as exc:
                if exc.detail not in SKIP: raise
                code, message = SKIP[exc.detail]
                items.append(dict(job_id=job.id, outcome='skipped', code=code, message=message,
                                  run_id=job.runs[-1].id, attempt=job.runs[-1].attempt))
        s.commit()
        return dict(items=items, retried=sum(x['outcome'] == 'retried' for x in items),
                    skipped=sum(x['outcome'] == 'skipped' for x in items))
