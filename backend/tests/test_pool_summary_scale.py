from sqlalchemy import create_engine,event,select
from backend.app import memberships
from backend.app.core import Slot,Worker,Pool,Run,now
from backend.tests.test_execution_logs_postgres import seed


def test_pool_summary_batches_reads_and_preserves_readiness(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'db'))
    factory=seed(engine)
    try:
        with factory() as s:
            for i in range(100):
                s.add(Slot(id='card'+str(i),worker_id='w',node_name='w',queue_name='q'+str(i),pool_id='p',
                    gpu_index=i+1,gpu_uuid='mock:'+str(i),gpu_model='virtual',vram_mb=1024,last_seen_at=now(),
                    enabled=i%3!=0,quarantined=i%7==0,desired_pool_id='missing' if i%11==0 else 'p',
                    membership_revision=1 if i%11==0 else 0,membership_applied_revision=0))
            s.get(Pool,'p').gpu_model='virtual'
            s.get(Slot,'s').gpu_uuid='mock:existing'
            run=s.get(Run,'r');run.status='FAILED';run.cleanup_confirmed=False
            s.commit()
        with factory() as s:
            expected=sum(memberships.view(s,x)['basic_ready'] for x in s.scalars(select(Slot)))
        queries=[]
        def count(*args):queries.append(args[2])
        event.listen(engine,'before_cursor_execute',count)
        with factory() as s:
            result=memberships.summary(s,s.get(Pool,'p'))
        event.remove(engine,'before_cursor_execute',count)
        assert result['member_count']==101
        assert result['ready_count']==expected
        assert result['active_jobs']==1  # Job still RUNNING; Run cleanup state is independent.
        assert len(queries)<=7,queries
        with factory() as s:
            s.get(Worker,'w').enabled=False;s.commit()
        with factory() as s:
            assert memberships.summary(s,s.get(Pool,'p'))['ready_count']==0
    finally:engine.dispose()
