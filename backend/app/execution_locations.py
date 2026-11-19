"""Attempt-specific, reported host paths. Never infer history from live settings."""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from .core import Job, Run, ExecutionData, Slot, Worker
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


def view(s, run):
    slot=s.get(Slot,run.slot_id) if run.slot_id else None
    worker=s.get(Worker,slot.worker_id) if slot else None
    records=s.scalars(select(ExecutionData).where(
        ExecutionData.run_id==run.id,ExecutionData.slot_id==run.slot_id,
        ExecutionData.execution_id==run.execution_id).order_by(ExecutionData.scanned_at.desc(),ExecutionData.id)).all() if run.execution_id else []
    return {'run_id':run.id,'execution_id':run.execution_id,
            'node_name':worker.node_name if worker else None,
            'directories':[{'id':d.id,'path':reported_path(d.root_path,d.entry_name,run.execution_id,(d.identity or {}).get("location","runs")),
                            'scan_status':d.scan_status,'cleanup_status':d.last_cleanup_status,
                            'scanned_at':d.scanned_at,'source':'node_inventory'} for d in records]}


def install(app, db, current):
    @app.get('/api/v1/jobs/{job_id}/attempts/{run_id}/execution-location')
    def location(job_id:str,run_id:str,user=Depends(current),s=Depends(db)):
        return view(s,visible_run(s,job_id,run_id,user))
