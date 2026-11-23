from alembic import command
from alembic.config import Config
from sqlalchemy import text, event
from backend.tests.test_execution_logs_postgres import pg, seed
from backend.app.execution_timing import pending_starts
from backend.app.core import Event,now


def test_pg_upgrade_invalid_index_retry_old_writer_and_downgrade(pg,monkeypatch):
    engine,url=pg;monkeypatch.setenv('DATABASE_URL',url);cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'aa741b2');factory=seed(engine,create=False)
    with factory() as s:
        s.add(Event(id='old-event',job_id='j',run_id='r',new_status='PENDING'));s.commit()
    command.upgrade(cfg,'ab852c3')
    with engine.connect() as c:
        assert c.execute(text("SELECT indisvalid FROM pg_index WHERE indexrelid='ix_job_events_run_status_created'::regclass")).scalar() is True
    # Recreate the catalog state left by an interrupted concurrent index build.
    with engine.begin() as c:
        c.execute(text("UPDATE pg_index SET indisvalid=false WHERE indexrelid='ix_job_events_run_status_created'::regclass"))
        c.execute(text("UPDATE alembic_version SET version_num='aa741b2'"))
    command.upgrade(cfg,'ab852c3');command.upgrade(cfg,'ab852c3')
    with factory() as s:
        # Old application inserts remain valid; no timing columns are mandatory.
        s.add(Event(id='after-upgrade',job_id='j',run_id='r',new_status='PENDING'));s.commit()
        assert pending_starts(s,['r'])['r']
    with engine.connect() as c:
        c.execute(text('SET enable_seqscan=off'))
        plan=' '.join(str(x) for x in c.execute(text("EXPLAIN SELECT min(created_at) FROM job_events WHERE run_id='r' AND new_status='PENDING' GROUP BY run_id")))
        assert 'ix_job_events_run_status_created' in plan
    command.downgrade(cfg,'aa741b2')
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('ix_job_events_run_status_created')")).scalar() is None
        assert c.execute(text('SELECT count(*) FROM job_events')).scalar()==2
    command.upgrade(cfg,'ab852c3')


def test_thousand_attempts_wait_lookup_uses_three_queries(pg):
    from backend.app.core import Run
    engine,_=pg;factory=seed(engine)
    stamp=now()
    with factory() as s:
        s.add_all([Run(id='bulk-'+str(i),job_id='j',attempt=i+2) for i in range(1001)]);s.flush()
        s.add_all([Event(job_id='j',run_id='bulk-'+str(i),new_status='PENDING',created_at=stamp) for i in range(1001)]);s.commit()
    queries=[]
    def trace(conn,cursor,statement,parameters,context,many):queries.append(statement)
    event.listen(engine,'before_cursor_execute',trace)
    try:
        with factory() as s:values=pending_starts(s,['bulk-'+str(i) for i in range(1001)])
    finally:event.remove(engine,'before_cursor_execute',trace)
    assert len(values)==1001 and len(queries)==3
    assert set(values.values())=={stamp}
