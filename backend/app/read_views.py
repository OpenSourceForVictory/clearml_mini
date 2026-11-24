"""Request-local projections for UI reads; never used by scheduling or writes."""
from collections import defaultdict
from types import SimpleNamespace
from sqlalchemy import select, func, and_
from sqlalchemy.orm import contains_eager
from .core import Job, Run, EpisodeRecord


def latest_jobs(s, batch_ids=None, *, summary=False, job_ids=None):
    if not batch_ids and not job_ids:
        return []
    condition = Job.id.in_(job_ids) if job_ids is not None else Job.batch_id.in_(batch_ids)
    latest = (select(Run.job_id, func.max(Run.attempt).label('attempt'))
              .join(Job, Job.id == Run.job_id).where(condition)
              .group_by(Run.job_id).subquery())
    join = and_(Run.job_id == latest.c.job_id, Run.attempt == latest.c.attempt)
    if not summary:
        # Read endpoints only: the relationship intentionally contains the latest
        # attempt. Never reuse these objects for mutations or history responses.
        return s.scalars(select(Job).join(latest, Job.id == latest.c.job_id).join(Run, join)
                         .options(contains_eager(Job.runs)).order_by(Job.created_at, Job.id)).unique().all()
    template = Job.client_spec['template']
    query = select(Job.id, Job.batch_id, Job.status, Job.episodes,
                   template['task_type'].label('task_type'),
                   template['episode_reporting'].label('episode_reporting'),
                   template['integration_mode'].label('integration_mode'),
                   template['execution_mode'].label('execution_mode'),
                   template['host_runtime']['episode_launch'].label('episode_launch'),
                   Run.id.label('run_id'), Run.status.label('run_status'), Run.exit_code,
                   Run.finished_at, Run.result_json['kind'].label('result_kind'),
                   Run.result_json['success'].label('result_success'))
    rows = s.execute(query.join(latest, Job.id == latest.c.job_id).join(Run, join)
                     .order_by(Job.created_at, Job.id)).mappings()
    result = []
    for row in rows:
        flags = {key: row[key] for key in ('task_type', 'episode_reporting', 'integration_mode', 'execution_mode')}
        flags['host_runtime'] = {'episode_launch': row['episode_launch']}
        report = {'kind': row['result_kind'], 'success': row['result_success']} if row['result_kind'] else None
        run = SimpleNamespace(id=row['run_id'], status=row['run_status'], exit_code=row['exit_code'],
                              finished_at=row['finished_at'], result_json=report)
        result.append(SimpleNamespace(id=row['id'], batch_id=row['batch_id'], status=row['status'],
                                     episodes=row['episodes'], client_spec={'template': flags}, runs=[run]))
    return result


def episode_summaries(s, run_ids):
    """Project result classification fields; retain legacy start-marker truthiness."""
    result = defaultdict(list)
    for offset in range(0, len(run_ids), 500):
        rows = s.execute(select(EpisodeRecord.run_id, EpisodeRecord.episode_id,
            EpisodeRecord.started.label('started'),
            EpisodeRecord.result['status'].label('status'), EpisodeRecord.result['success'].label('success'),
            EpisodeRecord.result['error'].label('error'), EpisodeRecord.result['exit_code'].label('exit_code'))
            .where(EpisodeRecord.run_id.in_(run_ids[offset:offset+500]))).mappings()
        for row in rows:
            event = {k:row[k] for k in ('status', 'success', 'error', 'exit_code')} if row['status'] else None
            result[row['run_id']].append(SimpleNamespace(episode_id=row['episode_id'],
                                                       started=row['started'], result=event))
    return result
