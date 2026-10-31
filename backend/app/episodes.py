"""Durable per-attempt episode results, independent of bounded text logs."""
from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from .core import EpisodeRecord, Job, Run
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
            if row.started != event:
                raise HTTPException(409, 'conflicting episode start')
            return
        s.add(EpisodeRecord(run_id=run.id, episode_id=event['episode_id'], seed=event['seed'], started=event))
    else:
        if not row:
            raise HTTPException(409, 'episode start is missing')
        if row.result is not None and row.result != event:
            raise HTTPException(409, 'conflicting episode result')
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
    def episodes(job_id: str, run_id: str, page: int = Query(1, ge=1), user=Depends(current), s: Session = Depends(db)):
        job, run = s.get(Job, job_id), s.get(Run, run_id)
        if not job or (job.owner_id != user.id and user.role != 'admin') or not run or run.job_id != job_id:
            raise HTTPException(404, 'attempt not found')
        rows = s.scalars(select(EpisodeRecord).where(EpisodeRecord.run_id == run_id)
                         .order_by(EpisodeRecord.episode_id).offset((page-1)*100).limit(100)).all()
        return {'planned': job.episodes, 'progress': progress(s, run), 'page': page,
                'items': [{'episode_id': r.episode_id, 'seed': r.seed, 'started': r.started,
                           'result': r.result, 'updated_at': r.updated_at} for r in rows]}
