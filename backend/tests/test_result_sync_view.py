from datetime import timedelta

from backend.app.core import ExecutionRecoveryControl, Run, RunLogState, Slot, now
from backend.app.result_sync_view import for_runs
from backend.tests.test_recovery_control import execution
from backend.tests.test_execution import env
from backend.tests.test_batch_controls import setup as batch_setup
from backend.app.scheduling import tick
from backend.app.recovery_records import grant


def test_recovery_pending_delayed_complete_and_log_gap(execution):
    factory, _ = execution
    at = now()
    with factory() as session:
        run = session.get(Run, 'r')
        run.status = 'SUCCEEDED'
        run.finished_at = at - timedelta(minutes=6)
        control = ExecutionRecoveryControl(execution_id='e', snapshot={})
        session.add(control)
        control.snapshot = {**control.snapshot, 'result_sync': 'pending'}
        session.add(RunLogState(run_id='r', truncated=True))
        session.commit()
        assert for_runs(session, [run], at=at)['r'] == {
            'state': 'pending', 'delayed': True, 'log_truncated': True, 'error': None}
        control.snapshot = {**control.snapshot, 'result_sync': 'complete'}
        session.commit()
        assert for_runs(session, [run], at=at)['r']['state'] == 'complete'
        assert not for_runs(session, [run], at=at)['r']['delayed']


def test_sync_delay_warning_starts_after_full_five_minutes(execution):
    factory, _ = execution
    at = now()
    with factory() as session:
        run = session.get(Run, 'r')
        run.status = 'FAILED'
        run.finished_at = at - timedelta(minutes=5)
        session.add(ExecutionRecoveryControl(execution_id='e', snapshot={'result_sync': 'pending'}))
        session.commit()
        assert not for_runs(session, [run], at=at)['r']['delayed']
        assert for_runs(session, [run], at=at + timedelta(microseconds=1))['r']['delayed']


def test_legacy_and_unconfirmed_recovery_are_distinct(execution):
    factory, _ = execution
    with factory() as session:
        run = session.get(Run, 'r')
        assert for_runs(session, [run])['r']['state'] == 'in_progress'
        run.status = 'FAILED'
        session.commit()
        assert for_runs(session, [run])['r']['state'] == 'unknown'
        from backend.app.core import ExecutionRecovery
        session.delete(session.get(ExecutionRecovery, 'e'))
        session.commit()
        assert for_runs(session, [run])['r']['state'] == 'legacy'


def test_failed_recovery_exposes_reason_without_inventing_delay(execution):
    factory, _ = execution
    with factory() as session:
        assert for_runs(session, []) == {}
        run = session.get(Run, 'r')
        control = ExecutionRecoveryControl(execution_id='e', snapshot={
            'result_sync': 'failed', 'result_sync_error': 'episode #0: invalid seed'})
        session.add(control)
        session.commit()
        assert for_runs(session, [run])['r'] == {
            'state': 'failed', 'delayed': False, 'log_truncated': False,
            'error': 'episode #0: invalid seed'}


def test_monitor_and_attempt_detail_show_same_recovery_state(env):
    main, client, auth = env
    batch, token, slots = batch_setup(env)
    assert tick(main.SessionLocal) == 2
    task = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token).json()['task']
    with main.SessionLocal() as session:
        run = session.get(Run, task['run_id'])
        worker_id = session.get(Slot, run.slot_id).worker_id
        recovery = grant(session, run, worker_id)
        session.add(ExecutionRecoveryControl(execution_id=recovery.execution_id,
                                             snapshot={'result_sync': 'pending'}))
        session.commit()
    monitor = client.get(f'/api/v1/batches/{batch["id"]}/monitor', headers=auth['alice']).json()
    item = next(case for case in monitor['cases'] if case['run_id'] == task['run_id'])
    detail = client.get(f'/api/v1/jobs/{item["job_id"]}', headers=auth['alice']).json()
    assert item['data_sync']['state'] == 'pending'
    assert detail['attempts'][-1]['data_sync'] == item['data_sync']
    assert all(case['data_sync']['state'] == 'legacy' for case in monitor['cases']
               if case['run_id'] != task['run_id'])
