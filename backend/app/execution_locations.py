"""Attempt-specific, reported host paths. Never infer history from live settings."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from .core import Job, Run, ExecutionData, Slot, Worker, SharedArtifactRoot, ExecutionBrowseRoot
from runner.process_log_contract import absolute_directory, identity


def visible_run(s, job_id, run_id, user):
    job=s.get(Job,job_id)
    run=s.get(Run,run_id)
    if not job or (user.role!='admin' and job.owner_id!=user.id) or not run or run.job_id!=job_id:
        raise HTTPException(404,'execution not found')
    return run


def reported_path(root, entry, execution, location="runs"):
    try:
        identity(execution,'task')
        absolute_directory(root)
        if entry!=execution or location not in ('runs','staging'):raise ValueError()
        return root.rstrip('/')+('/.cleanup/' if location=='staging' else '/')+entry
    except (ValueError,TypeError,AttributeError):
        return None


def output_location(s, run, records):
    """Metadata only: no mount access, mapping validation, lock or registration."""
    row = s.get(SharedArtifactRoot, run.id)
    if row:
        value = row.snapshot or {}
        if row.execution_id != run.execution_id:
            return None
        try:
            root = absolute_directory(value.get('output_root'))
        except (ValueError, TypeError):
            return None
        return dict(path=root + '/output' if value.get('layout') == 'batch-v1' else root,
                    execution_root=root, node_name=value.get('node_name'),
                    state='planned' if value.get('state') == 'planned' else 'reported')
    candidates = set()
    for record in records:
        if (record.identity or {}).get('location', 'runs') != 'runs':
            continue
        root = reported_path(record.root_path, record.entry_name, run.execution_id)
        if root:
            candidates.add((record.worker_id, root + '/output'))
    if len(candidates) != 1:
        return None
    worker_id, path = next(iter(candidates))
    worker = s.get(Worker, worker_id)
    return dict(path=path, execution_root=None, node_name=worker.node_name if worker else None,
                state='inventory')


def view(s, run):
    slot=s.get(Slot,run.slot_id) if run.slot_id else None
    worker=s.get(Worker,slot.worker_id) if slot else None
    records=s.scalars(select(ExecutionData).where(
        ExecutionData.run_id==run.id,ExecutionData.slot_id==run.slot_id,
        ExecutionData.execution_id==run.execution_id).order_by(ExecutionData.scanned_at.desc(),ExecutionData.id)).all() if run.execution_id else []
    browse=s.get(ExecutionBrowseRoot,run.id)
    browse_value=browse.snapshot if browse and browse.execution_id==run.execution_id else None
    return {'run_id':run.id,'execution_id':run.execution_id,
            'result_directory':({'path':browse_value.get('output_root'),'node_name':browse_value.get('node_name'),'error':browse_value.get('error')} if browse_value else None),
            'node_name':worker.node_name if worker else None,
            'output_directory':output_location(s,run,records),
            'directories':[{'id':d.id,'path':reported_path(d.root_path,d.entry_name,run.execution_id,(d.identity or {}).get("location","runs")),
                            'scan_status':d.scan_status,'cleanup_status':d.last_cleanup_status,
                            'scanned_at':d.scanned_at,'source':'node_inventory'} for d in records]}


def install(app, db, current):
    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/execution-location')
    def location(job_id:str,run_id:str,user=Depends(current),s=Depends(db)):
        return view(s,visible_run(s,job_id,run_id,user))
