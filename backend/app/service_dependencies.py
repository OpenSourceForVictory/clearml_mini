"""Read-only managed dependency selection, using persisted Worker health only."""
from datetime import timedelta
from sqlalchemy import select
from .core import ServiceInstance,EvaluationOrchestration,Job,Run,Worker,Slot,now
from .scheduling import online,utc


def current_run(s,instance):
    return s.scalar(select(Run).where(Run.job_id==instance.current_job_id).order_by(Run.attempt.desc())) if instance.current_job_id else None


def ready(s,instance,at=None):
    at=at or now()
    if not instance or not instance.desired_running or not instance.health_ready or not instance.health_at:
        return False
    from .service_readiness import fresh
    if not fresh(instance,at):return False
    slot=s.get(Slot,instance.slot_id);worker=s.get(Worker,instance.worker_id)
    run=current_run(s,instance)
    return bool(worker and worker.enabled and not worker.revoked_at and slot and slot.enabled and online(slot,at) and run and
                run.status=='RUNNING' and run.lease_expires_at and utc(run.lease_expires_at)>at)


def candidate(s,instance,definition):
    run=current_run(s,instance)
    return {'url':f"{instance.template_snapshot['protocol']}://{instance.address}:{instance.port}",'mode':definition['mode'],
            'service_instance_id':instance.id,'service_execution_id':run.execution_id if run else None,'service_key':instance.service_key}


def candidates(s,spec):
    if s is None:return []
    identity=spec.get('orchestration_batch_id');orch=s.get(EvaluationOrchestration,identity) if identity else None
    if not orch:return []
    definitions={d['key']:d for d in orch.definition['services']}
    allowed=set(spec.get('service_keys',definitions))
    return [candidate(s,i,definitions[i.service_key]) for i in s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==identity).order_by(ServiceInstance.created_at,ServiceInstance.id))
            if i.service_key in allowed and i.service_key in definitions and ready(s,i)]


def binding_ready(s,run,at=None):
    if not run.assigned_service_instance_id:return True
    item=s.get(ServiceInstance,run.assigned_service_instance_id)
    current=current_run(s,item) if item else None
    return bool(current and current.execution_id==run.assigned_service_execution_id and ready(s,item,at))


def bind(run,selection):
    run.assigned_service_instance_id=selection.get('service_instance_id')
    run.assigned_service_execution_id=selection.get('service_execution_id')
    run.assigned_service_key=selection.get('service_key')
