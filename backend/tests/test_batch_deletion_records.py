from backend.tests.test_execution import env
from backend.tests.test_batch_controls import setup, control
from backend.tests.test_batch_deletion import delete
from backend.app.core import (Slot, RunLog, EpisodeRecord, RunArtifact, ExecutionData,
                              DataCleanup, DataCleanupItem, Job, Run, now)
from sqlalchemy import event, select

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
