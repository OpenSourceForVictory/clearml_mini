"""Read-only, bounded batch reports; no scheduler lock or event payload export."""
from . import endpoints
import csv
import hashlib
import io
import json
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select, func, case, and_
from sqlalchemy.orm import Session

from .core import Job, Run, EpisodeRecord
from .scheduling import ACTIVE, TERMINAL
from .monitoring import owned


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


@contextmanager
def snapshot(engine):
    with engine.connect() as conn:
        if conn.dialect.name == 'postgresql':
            conn = conn.execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True)
            conn.begin()
        else:
            conn.exec_driver_sql('BEGIN')
        try:
            with Session(bind=conn) as session:
                yield session
        finally:
            conn.rollback()


def report(s, batch, at):
    latest = select(Run.job_id, func.max(Run.attempt).label('attempt')).join(Job, Job.id == Run.job_id).where(Job.batch_id == batch.id).group_by(Run.job_id).subquery()
    rows = s.execute(select(Job, Run).join(latest, latest.c.job_id == Job.id)
                     .join(Run, and_(Run.job_id == Job.id, Run.attempt == latest.c.attempt))
                     .where(Job.batch_id == batch.id).order_by(Job.case_id, Job.id)).all()
    completed = EpisodeRecord.result['status'].as_string() == 'COMPLETED'
    success = EpisodeRecord.result['success'].as_boolean().is_(True)
    counts = {r[0]: r[1:] for r in s.execute(select(EpisodeRecord.run_id,
        func.sum(case((completed, 1), else_=0)),
        func.sum(case((and_(completed, success), 1), else_=0)),
        func.sum(case((EpisodeRecord.result['status'].as_string() == 'ERROR', 1), else_=0)))
        .where(EpisodeRecord.run_id.in_([run.id for _, run in rows])).group_by(EpisodeRecord.run_id))}
    cases = []
    for job, run in rows:
        spec = job.client_spec
        template = spec['template']
        reporting = template.get('episode_reporting', False)
        done, successes, errors = counts.get(run.id, (0, 0, 0)) if reporting else (0, 0, 0)
        definition = dict(task_name=spec['task_name'], seed=spec['seed'], episodes=job.episodes,
                          parameters_digest=digest(spec.get('parameters', {})))
        config = dict(pool_id=job.pool_id, gpu_mode=job.gpu_mode,
                      requested_vram_gb=job.requested_vram_gb, required_gpu_model=job.required_gpu_model,
                      **endpoints.view(run, spec), client_name=template['name'], client_version=template['version'],
                      image=template['image'], environment_digest=digest({
                          'runtime': (template.get('runtime') or {}).get('environment', {}),
                          'overrides': spec.get('environment', {})}))
        cases.append(dict(job_id=job.id, case_id=job.case_id, run_id=run.id, attempt=run.attempt,
                          status=job.status, **definition, definition_digest=digest(definition), config=config,
                          episode_reporting=reporting, completed_episodes=done, successes=successes,
                          goal_failures=done-successes, episode_errors=errors,
                          success_rate=successes/done if done else None,
                          partial=not reporting or done != job.episodes or errors > 0 or job.status != 'SUCCEEDED'))
    done = sum(c['completed_episodes'] for c in cases)
    successes = sum(c['successes'] for c in cases)
    summary = dict(total_tasks=len(cases), planned_episodes=sum(c['episodes'] for c in cases),
                   completed_episodes=done, successes=successes,
                   goal_failures=done-successes, episode_errors=sum(c['episode_errors'] for c in cases),
                   reporting_tasks=sum(c['episode_reporting'] for c in cases),
                   unreported_tasks=sum(not c['episode_reporting'] for c in cases),
                   partial=any(c['partial'] for c in cases), success_rate=successes/done if done else None)
    states = [c['status'] for c in cases]
    status = ('SUCCEEDED' if states and all(x == 'SUCCEEDED' for x in states) else
              'RUNNING' if any(x in ACTIVE for x in states) else
              'PENDING' if any(x not in TERMINAL for x in states) else
              'FAILED' if any(x in ('FAILED', 'TIMED_OUT') for x in states) else 'CANCELLED')
    return dict(schema_version=1, generated_at=at, batch=dict(id=batch.id, title=batch.title,
                suite_id=batch.suite_id, status=status, scheduling_paused=batch.scheduling_paused, model_version_verified=False), summary=summary, cases=cases)


def read_reports(engine, ids, user):
    with snapshot(engine) as s:
        batches = [owned(s, identity, user) for identity in ids]
        at = datetime.now(timezone.utc).isoformat()
        return [report(s, batch, at) for batch in batches]


def compare(left, right):
    sides = [{c['case_id']: c for c in r['cases']} for r in (left, right)]
    rows = []
    for identity in sorted(sides[0].keys() | sides[1].keys()):
        a, b = (side.get(identity) for side in sides)
        reasons, definitions, configs = [], [], []
        if a is None or b is None:
            reasons.append('missing_left' if a is None else 'missing_right')
        else:
            definitions = [k for k in ('task_name', 'seed', 'episodes', 'parameters_digest') if a[k] != b[k]]
            configs = [k for k in a['config'] if a['config'][k] != b['config'][k]]
            if definitions:
                reasons.append('definition_mismatch')
            if not a['episode_reporting'] or not b['episode_reporting']:
                reasons.append('unreported')
            if a['partial'] or b['partial']:
                reasons.append('incomplete')
        rows.append(dict(case_id=identity, left=a, right=b, reasons=reasons,
                         definition_differences=definitions, config_differences=configs,
                         delta_success_rate=b['success_rate']-a['success_rate'] if not reasons else None))
    eligible = bool(rows) and all(not row['reasons'] for row in rows)
    return dict(left=left, right=right, cases=rows, comparable=eligible,
                delta_success_rate=right['summary']['success_rate']-left['summary']['success_rate'] if eligible else None)


CSV_COLUMNS = ['schema_version', 'generated_at', 'batch_id', 'batch_title', 'case_id', 'job_id', 'run_id',
               'attempt', 'task_name', 'seed', 'episodes', 'status', 'episode_reporting', 'partial',
               'completed_episodes', 'successes', 'goal_failures', 'episode_errors', 'success_rate',
               'parameters_digest', 'definition_digest', 'endpoint', 'endpoint_mode', 'endpoint_candidates']


def csv_text(value):
    if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or
                                  any(unicodedata.category(c) == 'Cc' for c in value[:len(value)-len(value.lstrip())+1])):
        return "'" + value
    return value


def export_csv(value):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for item in value['cases']:
        row = dict(item, schema_version=value['schema_version'], generated_at=value['generated_at'],
                   batch_id=value['batch']['id'], batch_title=value['batch']['title'])
        row.update(item.get('config', {}))
        row['endpoint_candidates'] = json.dumps(row.get('endpoint_candidates', []), ensure_ascii=False)
        writer.writerow({k: csv_text(row.get(k)) for k in CSV_COLUMNS})
    return ('\ufeff' + stream.getvalue()).encode('utf-8')


def install(app, db, current):
    @app.get('/api/v1/batches/{batch_id}/report')
    def download(batch_id: str, format: Literal['json', 'csv'] = 'json', user=Depends(current), s=Depends(db)):
        value = read_reports(s.get_bind(), [batch_id], user)[0]
        filename = 'batch-' + value['batch']['id'] + '.' + format
        body = export_csv(value) if format == 'csv' else json.dumps(value, ensure_ascii=False).encode()
        return Response(body, media_type='text/csv' if format == 'csv' else 'application/json',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/api/v1/batch-comparisons')
    def comparison(left_id: str, right_id: str, user=Depends(current), s=Depends(db)):
        if left_id == right_id:
            raise HTTPException(422, 'select two different batches')
        return compare(*read_reports(s.get_bind(), [left_id, right_id], user))
