from backend.tests.test_execution import env
from backend.tests.test_batch_controls import setup, control
from backend.tests.test_batch_deletion import delete
from backend.app.core import (Slot, RunLog, EpisodeRecord, RunArtifact, ExecutionData,
                              DataCleanup, DataCleanupItem, Job, Run, now)
from sqlalchemy import event, select
import pytest

def test_populated_batch_deletion_with_foreign_keys(env):
    main, c, auth = env
    b, _, slots = setup(env)
    control(env, b, 'cancel', 0)
    run_id = b['cases'][0]['run_id']
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0])
        s.add(RunLog(run_id=run_id, sequence=1, content='log'))
        s.add(EpisodeRecord(run_id=run_id, episode_id=0, seed=0, started={}))
        s.add(RunArtifact(run_id=run_id, name='result', sha256='0'*64, size_bytes=1, data=b'x'))
        data = ExecutionData(worker_id=slot.worker_id, slot_id=slot.id, run_id=run_id, root_id='root', root_path='/tmp/test', entry_name='test', generation='g', scan_status='complete')
        op = DataCleanup(worker_id=slot.worker_id, storage_revision=1, expires_at=now())
        s.add_all([data, op]); s.flush()
        s.add(DataCleanupItem(operation_id=op.id, data_id=data.id, slot_id=slot.id, snapshot={}, status='completed'))
        s.commit()
    def fk(conn, record, proxy): conn.execute('PRAGMA foreign_keys=ON')
    engine = main.SessionLocal.kw['bind']
    event.listen(engine, 'checkout', fk)
    try:
        assert delete(env, b).status_code == 200
        with main.SessionLocal() as s:
            for model in (Job, Run, RunLog, EpisodeRecord, RunArtifact, ExecutionData, DataCleanupItem):
                assert not list(s.scalars(select(model)))
    finally:
        event.remove(engine, 'checkout', fk)


@pytest.mark.parametrize('fail_audit', [False, True])
def test_scheduled_service_batch_deletion_with_foreign_keys(env, monkeypatch, fail_audit):
    from backend.tests.test_orchestrations import setup as managed_setup, submit
    from backend.app.core import ServiceInstance, EvaluationOrchestration, EvaluationBatch
    from backend.app.scheduling import tick

    main, _, _ = env
    *_, body = managed_setup(env, monkeypatch)
    batch = submit(env, body, 'delete-scheduled-services')
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        services = list(s.scalars(select(ServiceInstance).where(
            ServiceInstance.orchestration_batch_id == batch['id'])))
        assert services, 'must exercise actual service records, not only a definition'
        service_ids = [item.id for item in services]
        jobs = list(s.scalars(select(Job).where(Job.service_instance_id.in_(service_ids))))
        assert jobs, 'must exercise service jobs referencing those records'
        service_job_ids = [job.id for job in jobs]
    assert control(env, batch, 'cancel', 0).status_code == 200
    # Simulate confirmed Worker cleanup; do not bypass the endpoint guard.
    with main.SessionLocal() as s:
        for run in s.scalars(select(Run)):
            run.cleanup_confirmed = True
        for job in s.scalars(select(Job)):
            job.status = 'CANCELLED'
        for item in s.scalars(select(ServiceInstance)):
            item.desired_running = False
        s.commit()

    def fk(conn, record, proxy):
        conn.execute('PRAGMA foreign_keys=ON')

    engine = main.SessionLocal.kw['bind']
    event.listen(engine, 'checkout', fk)
    try:
        if fail_audit:
            from backend.app import administration

            def fail(*args, **kwargs):
                raise RuntimeError('injected audit failure')

            with monkeypatch.context() as patch:
                patch.setattr(administration, 'audit', fail)
                with pytest.raises(RuntimeError, match='injected audit failure'):
                    delete(env, batch)
            with main.SessionLocal() as s:
                assert s.get(EvaluationBatch, batch['id']).deleted_at is None
                assert s.get(EvaluationOrchestration, batch['id']) is not None
                assert all(s.get(ServiceInstance, identity) is not None for identity in service_ids)
                assert all(s.get(Job, identity) is not None for identity in service_job_ids)
                assert list(s.scalars(select(Run)))
        response = delete(env, batch)
        assert response.status_code == 200, response.text
        assert delete(env, batch).status_code == 200
        with main.SessionLocal() as s:
            assert s.get(EvaluationBatch, batch['id']).deleted_at is not None
            assert s.get(EvaluationOrchestration, batch['id']) is None
            assert all(s.get(ServiceInstance, identity) is None for identity in service_ids)
            assert all(s.get(Job, identity) is None for identity in service_job_ids)
            assert not list(s.scalars(select(Run)))
    finally:
        event.remove(engine, 'checkout', fk)
