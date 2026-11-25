"""Authenticated control reconciliation, never a shortcut to resource release."""
from datetime import timedelta
import hashlib
import json
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .core import ExecutionRecovery, ExecutionRecoveryControl, Job, Run, ServiceInstance, Slot, now
from .scheduling import TERMINAL, execution_timeout, fast_lock, seconds, transition, utc


class ProcessIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['host', 'docker']
    boot_id: str = Field(min_length=1, max_length=128)
    # systemd InvocationID or full Docker container ID, not a reusable PID/name.
    runtime_id: str = Field(pattern=r'^[0-9a-f]{32}$|^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def runtime_identity(self):
        if len(self.runtime_id) != (32 if self.kind == 'host' else 64):
            raise ValueError('runtime identity does not match execution mode')
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: str = Field(min_length=1, max_length=36)
    sequence: int = Field(strict=True, ge=1, lt=2**63)
    identity: ProcessIdentity
    state: Literal['running', 'exited', 'missing']
    elapsed_seconds: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    exit_code: int | None = Field(default=None, strict=True)
    stop_reason: Literal['disconnection_timeout', 'execution_timeout', 'cancelled', 'node_restarted'] | None = None

    @model_validator(mode='after')
    def valid_exit(self):
        if self.state == 'running' and self.elapsed_seconds is None:
            raise ValueError('running observation requires persisted elapsed seconds')
        if self.exit_code is not None and self.state != 'exited':
            raise ValueError('only an exited process may report an exit code')
        return self


def platform_stop_reason(run, grant):
    """Committed platform lifecycle overrides worker observations and old replies."""
    if run.status in ('CANCEL_REQUESTED', 'CANCELLED'):
        return 'cancelled'
    if run.status == 'TIMED_OUT':
        return 'execution_timeout'
    if run.status in TERMINAL:
        return 'platform_terminal'
    if grant.revoked:
        return 'assignment_revoked'
    return None


def reconcile(s, *, worker_id, execution_id, observation):
    # Cheap authentication/identity checks before contending with allocation.
    grant = s.get(ExecutionRecovery, execution_id)
    if not grant or grant.worker_id != worker_id:
        raise HTTPException(404, 'recovery execution not found')
    if grant.generation != observation.generation:
        raise HTTPException(409, 'recovery assignment generation does not match')
    # Same order as cancellation and allocation: scheduler -> Run -> grant.
    # Receipt writers lock only grant and never lock Run.
    fast_lock(s)
    run = s.scalar(select(Run).where(Run.id == grant.run_id).with_for_update()
                   .execution_options(populate_existing=True))
    grant = s.scalar(select(ExecutionRecovery).where(ExecutionRecovery.execution_id == execution_id)
                     .with_for_update().execution_options(populate_existing=True))
    control = s.get(ExecutionRecoveryControl, execution_id)
    previous = control.snapshot if control else {}
    data = observation.model_dump(mode='json')
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    slot = s.get(Slot, grant.slot_id)
    owns = bool(run and run.execution_id == execution_id and run.slot_id == grant.slot_id
                and slot and slot.worker_id == worker_id)
    # Replayed responses never renew the lease, nor grant new control time.
    if observation.sequence <= previous.get('sequence', 0):
        if observation.sequence == previous['sequence'] and digest == previous['digest']:
            reply = {**previous['reply'], 'duplicate': True,
                     'platform_status':run.status if owns else None}
            # New cancellation/revocation overrides a cached tracking reply.
            # It still cannot renew or grant new request sequence authority.
            if (owns and previous.get('identity') == observation.identity.model_dump()
                    and not previous.get('identity_conflict')
                    and platform_stop_reason(run, grant)):
                reply.update(action='stop', reason=platform_stop_reason(run, grant))
                control.snapshot = {**previous, 'reply':reply, 'stop_requested':True,
                                    'stop_reason':reply['reason']}
                s.flush()
            return reply
        raise HTTPException(409, 'stale or conflicting recovery control observation')
    identity = observation.identity.model_dump()
    at = now()
    action, reason = 'observe', None
    mismatch = previous.get('identity') is not None and previous['identity'] != identity
    launch = previous.get('launch')
    if launch:
        mismatch = mismatch or any(launch['request'][key] != identity[key] for key in ('kind', 'boot_id'))
    # Do not direct a worker to stop an unrelated/reused runtime identity.
    if mismatch or not owns or previous.get('identity_conflict'):
        action, reason = 'inspect', 'process_or_assignment_identity_changed'
    elif previous.get('observed_state') in ('exited', 'missing') and observation.state == 'running':
        action, reason = 'inspect', 'process_completion_is_irreversible'
    elif (previous.get('observed_state') == 'exited' and observation.state == 'exited'
          and previous.get('exit_code') != observation.exit_code):
        action, reason = 'inspect', 'process_exit_code_changed'
    elif platform_stop_reason(run, grant):
        action, reason = 'stop', platform_stop_reason(run, grant)
    elif previous.get('stop_requested'):
        action, reason = 'stop', previous['stop_reason']
    elif run.status not in ('STARTING', 'RUNNING'):
        action, reason = 'inspect', 'execution_not_claimed'
    elif observation.stop_reason:
        action, reason = 'stop', observation.stop_reason
    elif observation.state == 'running' and observation.elapsed_seconds < previous.get('elapsed_seconds', 0):
        action, reason = 'inspect', 'elapsed_time_moved_backwards'
    elif observation.state != 'running':
        action, reason = 'retain', 'exit_or_absence_recorded_pending_cleanup'
    else:
        job = s.get(Job, run.job_id)
        limit = execution_timeout(job)
        try:
            estimated_start = at - timedelta(seconds=observation.elapsed_seconds)
        except OverflowError:
            raise HTTPException(422, 'elapsed time is outside the supported timestamp range') from None
        started_at = min(utc(run.started_at), estimated_start) if run.started_at else estimated_start
        tolerance = grant.policy['disconnect_tolerance_seconds']
        control_at = utc(run.enqueued_at)
        if previous.get('confirmed_at'):
            from datetime import datetime
            control_at = datetime.fromisoformat(previous['confirmed_at'])
        if tolerance is not None and control_at is None:
            action, reason = 'inspect', 'missing_original_control_time'
        elif tolerance is not None and (at-control_at).total_seconds() >= tolerance:
            action, reason = 'stop', 'disconnection_timeout'
        elif job.benchmark != 'inference-service' and limit > 0 and at >= started_at + timedelta(seconds=limit):
            action, reason = 'stop', 'execution_timeout'
        else:
            # First binding is only legal while the original claim is STARTING.
            if not previous.get('identity') and run.status != 'STARTING':
                action, reason = 'inspect', 'missing_original_process_identity'
            else:
                expired = not run.lease_expires_at or utc(run.lease_expires_at) <= at
                if job.service_instance_id and expired:
                    instance = s.get(ServiceInstance, job.service_instance_id)
                    if instance and instance.current_job_id == job.id:
                        instance.health_ready = False
                        instance.health_at = None
                        instance.probe_state = {**(instance.probe_state or {}), 'epoch': None, 'sequence': 0}
                if run.status == 'STARTING':
                    transition(s, run, 'RUNNING')
                    # Preserve an already known business start; never reset it.
                    run.started_at = started_at
                    job.started_at = job.started_at or started_at
                run.lease_expires_at = at + timedelta(seconds=seconds('LEASE_SECONDS', 30))
                action = 'track'
    reply = {'execution_id': execution_id, 'generation': grant.generation,
             'sequence': observation.sequence, 'action': action, 'reason': reason,
             'duplicate': False, 'platform_status':run.status if owns else None, 'cleanup_confirmed': bool(owns and run.cleanup_confirmed)}
    # An unknown identity cannot replace the trusted binding or erase a stop.
    snapshot = {**previous, 'sequence': observation.sequence, 'digest': digest, 'reply': reply}
    if action != 'inspect':
        snapshot.update(identity=identity, observed_state=observation.state, exit_code=observation.exit_code,
                        worker_stop_reason=observation.stop_reason)
        if previous.get('observed_state') == 'exited' and observation.state == 'missing':
            snapshot.update(observed_state='exited', exit_code=previous.get('exit_code'))
    if action == 'inspect':
        snapshot['identity_conflict'] = True
    if observation.elapsed_seconds is not None and action != 'inspect':
        snapshot['elapsed_seconds'] = observation.elapsed_seconds
    if action == 'stop':
        snapshot.update(stop_requested=True, stop_reason=reason)
    if action == 'track':
        snapshot['confirmed_at'] = at.isoformat()
    if control:
        control.snapshot = snapshot
    else:
        s.add(ExecutionRecoveryControl(execution_id=execution_id, snapshot=snapshot))
    s.flush()
    return reply


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/recovery/reconcile')
    def control(execution_id: str, body: Observation, worker=Depends(worker_auth), s=Depends(db)):
        reply = reconcile(s, worker_id=worker.id, execution_id=execution_id, observation=body)
        s.commit()
        return reply
