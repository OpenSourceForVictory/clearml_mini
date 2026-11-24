"""Recovery authorization must preserve ownership without hiding business limits."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app import recovery_records, scheduling
from backend.app.core import ExecutionRecovery, Job, Run, Slot, now
from backend.tests.test_scheduling import farm, job
from backend.tests.test_execution_logs_postgres import pg


def claimed(factory, *, tolerance=None):
    jid = job(factory, 'a', 'small')
    assert scheduling.tick(factory) == 1
    with factory() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        run.status = s.get(Job, jid).status = 'STARTING'
        run.cleanup_confirmed = False
        record = recovery_records.grant(s, run, 'single', tolerance)
        execution_id = run.execution_id
        run.lease_expires_at = now() - timedelta(days=365)
        run.enqueued_at = now() - timedelta(days=365)
        s.get(Job, jid).client_spec = {'timeout_seconds': 0}
        s.commit()
        return jid, execution_id


@pytest.mark.parametrize('status', ['STARTING', 'RUNNING', 'CANCEL_REQUESTED'])
@pytest.mark.parametrize('tolerance', [None, 1])
@pytest.mark.parametrize('revoked', [False, True])
def test_disconnect_never_proves_stop_or_releases_resources(farm, status, tolerance, revoked):
    jid, execution = claimed(farm, tolerance=tolerance)
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        run.status = s.get(Job, jid).status = status
        s.get(ExecutionRecovery, execution).revoked = revoked
        s.commit()
    waiting = job(farm, 'b', 'small')
    for _ in range(2):
        assert scheduling.tick(farm) == 0
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        assert (run.status, run.execution_id, run.slot_id) == (status, execution, 'single-0')
        assert not run.cleanup_confirmed
        assert s.get(Job, waiting).status == 'PENDING'
        assert run.finished_at is None


@pytest.mark.parametrize('broken', ['absent', 'execution', 'run', 'slot'])
def test_legacy_or_other_assignment_does_not_gain_recovery(farm, broken):
    jid, execution = claimed(farm)
    with farm() as s:
        grant = s.get(ExecutionRecovery, execution)
        if broken == 'absent':
            s.delete(grant)
        else:
            setattr(grant, {'execution':'execution_id', 'run':'run_id', 'slot':'slot_id'}[broken], 'other')
        s.commit()
    scheduling.tick(farm)
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        assert run.status == 'TIMED_OUT'
        assert not run.cleanup_confirmed
        assert s.get(Slot, run.slot_id).quarantined


def test_recovery_does_not_disable_business_execution_deadline(farm):
    jid, execution = claimed(farm)
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        run.status = 'RUNNING'
        run.started_at = now() - timedelta(seconds=120)
        s.get(Job, jid).client_spec = {'timeout_seconds': 60}
        s.commit()
    scheduling.tick(farm)
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        assert run.status == 'TIMED_OUT' and '60 秒' in run.error
        assert run.execution_id == execution and not run.cleanup_confirmed


def test_unclaimed_reservation_still_expires(farm):
    jid = job(farm, 'a')
    scheduling.tick(farm)
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        run.reservation_expires_at = now() - timedelta(seconds=1)
        scheduling.reconcile(s, now())
        assert run.status == 'PENDING'
        assert run.execution_id is None and run.slot_id is None


@pytest.mark.parametrize('status', ['STARTING', 'RUNNING', 'CANCEL_REQUESTED'])
def test_postgres_reconcile_preserves_negotiated_execution(pg, status):
    from backend.tests.test_execution_logs_postgres import seed
    factory = seed(pg[0])
    with factory() as s:
        run = s.get(Run, 'r')
        run.status = 'STARTING'
        recovery_records.grant(s, run, 'w')
        run.status = status
        run.enqueued_at = now() - timedelta(days=365)
        run.lease_expires_at = now() - timedelta(days=365)
        s.get(Job, 'j').client_spec = {'timeout_seconds': 0}
        s.commit()
    with factory() as s:
        scheduling.lock(s)
        scheduling.reconcile(s, now())
        s.commit()
        assert s.get(Run, 'r').status == status
        assert s.get(Run, 'r').execution_id == 'e'
        assert not s.get(Run, 'r').cleanup_confirmed


def test_disconnected_service_keeps_ownership_but_is_not_ready_for_new_clients(farm):
    from backend.app.core import ServiceInstance
    from backend.app.service_dependencies import ready
    jid, execution = claimed(farm)
    at = now()
    with farm() as s:
        run = s.scalar(select(Run).where(Run.job_id == jid))
        run.status = 'RUNNING'
        run.started_at = at - timedelta(days=365)
        s.get(Job, jid).benchmark = 'inference-service'
        # A fresh node heartbeat or health sample alone cannot restore this
        # execution's expired control lease.
        s.get(Slot, run.slot_id).last_seen_at = at
        service = ServiceInstance(id='recovery-service', name='service',
            worker_id='single', slot_id=run.slot_id, address='127.0.0.1', port=19000,
            current_job_id=jid, template_snapshot={}, desired_running=True,
            health_ready=True, health_at=at)
        s.add(service); s.flush()
        scheduling.reconcile(s, at)
        assert run.status == 'RUNNING' and run.execution_id == execution
        assert not ready(s, service, at)
        assert not run.cleanup_confirmed
