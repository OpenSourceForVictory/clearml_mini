"""Durable per-attempt episode results, independent of bounded text logs."""
from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from .core import EpisodeRecord, Job, Run, now
from . import execution_timing as timing
from runner.episode_contract import validate_event
from runner.sdk import episode_seed


def record(s, run, event):
    try:
        validate_event(event)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(400, str(exc))
    job = s.get(Job, run.job_id)
    if not job.client_spec or event['episode_id'] > job.episodes or event['seed'] != episode_seed(job.client_spec['seed'], event['episode_id']):
        raise HTTPException(400, 'episode does not match execution plan')
    row = s.get(EpisodeRecord, (run.id, event['episode_id']))
    if event['status'] == 'STARTED':
        if row:
            if timing.original(row.started) != event:
                raise HTTPException(409, 'conflicting episode start')
            return
        s.add(EpisodeRecord(run_id=run.id, episode_id=event['episode_id'], seed=event['seed'], started={**event,timing.RECEIVED:now().isoformat()}))
    else:
        if not row:
            raise HTTPException(409, 'episode start is missing')
        if row.result is not None and row.result != event:
            raise HTTPException(409, 'conflicting episode result')
        if event.get("timing") and (row.started or {}).get("timing") and event["timing"]["started_at"] != row.started["timing"]["started_at"]:
            raise HTTPException(409, "conflicting episode timing start")
        row.result = event
    s.flush()


def progress(s, run):
    rows = s.scalars(select(EpisodeRecord).where(EpisodeRecord.run_id == run.id)).all()
    return summarize(rows)


def summarize(rows):
    completed = sum(bool(r.result and r.result['status'] == 'COMPLETED') for r in rows)
    successes = sum(bool(r.result and r.result.get('success')) for r in rows)
    errors = sum(bool(r.result and r.result['status'] == 'ERROR') for r in rows)
    return {'completed': completed, 'successes': successes, 'errors': errors,
            'running': [r.episode_id for r in rows if not r.result],
            'success_rate': successes/completed if completed else None}


def install(app, db, current):
    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/episodes')
    def episodes(job_id: str, run_id: str, page: int = Query(1, ge=1), include_planned: bool = False, user=Depends(current), s: Session = Depends(db)):
        job, run = s.get(Job, job_id), s.get(Run, run_id)
        if not job or (job.owner_id != user.id and user.role != 'admin') or not run or run.job_id != job_id:
            raise HTTPException(404, 'attempt not found')
        at=now()
        template=(job.client_spec or {}).get('template',{})
        from .result_views import load_records, summarize_attempt, episode_view
        all_rows=load_records(s,[run.id])[run.id]
        outcomes=summarize_attempt(job,run,all_rows)
        start=(page-1)*100+1
        query=select(EpisodeRecord).where(EpisodeRecord.run_id==run_id)
        query=(query.where(EpisodeRecord.episode_id>=start,EpisodeRecord.episode_id<start+100)
               if include_planned else query.order_by(EpisodeRecord.episode_id).offset((page-1)*100).limit(100))
        rows=list(s.scalars(query.order_by(EpisodeRecord.episode_id)))
        items={r.episode_id:{'episode_id':r.episode_id,'seed':r.seed,'timing':timing.episode(template,r,run.status,at),'started':timing.original(r.started),'result':r.result,'updated_at':r.updated_at, **episode_view(template,r,run.status)} for r in rows}
        if include_planned and outcomes['detail_mode'] != 'execution':
            for i in range(start,min(start+100,job.episodes+1)):
                items.setdefault(i,{'episode_id':i,'seed':episode_seed((job.client_spec or {}).get('seed',0),i),'timing':timing.episode(template,None,run.status,at),'started':None,'result':None,'updated_at':None, **episode_view(template,None,run.status)})
        return {'timing':timing.attempt(job,run,at,timing.pending_starts(s,[run.id]).get(run.id)), 'outcomes':outcomes,'planned':job.episodes,'progress':summarize(all_rows),'page':page,
                'exit_code':run.exit_code,'run_status':run.status,'run_error':run.error,'episode_reporting':bool(template.get('episode_reporting')),
                'result_source':'exit_code' if template.get('task_type')=='command' else 'protocol',
                'items':[items[i] for i in sorted(items)]}
