"""Single readiness policy used by scheduler and service presentation."""
from datetime import timedelta
from .core import now
from .scheduling import utc
from runner.readiness_contract import normalize


def fresh(item,at=None):
    at=at or now()
    return bool(item.health_at and timedelta(0)<=at-utc(item.health_at)<timedelta(seconds=normalize(item.template_snapshot)['stale_after']))


def accept(item,body):
    at=now();state=dict(item.probe_state or {});config=normalize(item.template_snapshot)
    if body.epoch!=state.get('epoch'):return 'epoch'
    if body.sequence<=state.get('sequence',0):return 'duplicate'
    if body.sample_age_ms/1000>=config['stale_after']:return 'stale'
    if body.sequence!=state.get('sequence',0)+1:
        state.update(successes=0,failures=0)
    if not fresh(item,at):
        state.update(successes=0,failures=0);item.health_ready=False
    good=state.get('successes',0)+1 if body.ready else 0
    bad=state.get('failures',0)+1 if not body.ready else 0
    if good>=config['success_threshold']:item.health_ready=True;state['ever_ready']=True
    if bad>=config['failure_threshold']:item.health_ready=False
    state.update(sequence=body.sequence,successes=good,failures=bad,phase=body.phase,
                 duration_ms=body.duration_ms,error_code=body.error_code)
    item.health_at=at-timedelta(milliseconds=body.sample_age_ms)
    item.health_error=body.error_code
    item.probe_state=state
    return 'accepted'


def diagnostics(item):
    state=item.probe_state or {};c=normalize(item.template_snapshot)
    return {k:state.get(k) for k in ('successes','failures','phase','duration_ms','error_code','ever_ready')} | {
        'kind':c['kind'],'mode':c['mode'],'success_threshold':c['success_threshold'],
        'failure_threshold':c['failure_threshold'],'stale_after':c['stale_after']}
