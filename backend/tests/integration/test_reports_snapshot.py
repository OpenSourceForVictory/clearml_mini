"""A committed writer interleaves with report reads without blocking or mixing snapshots."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from sqlalchemy import event, text
from backend.app.core import User, Pool, EvaluationBatch, Job, Run, EpisodeRecord, session_factory
from backend.app.reports import read_reports, compare

@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='dedicated PostgreSQL required')
@pytest.mark.parametrize('mutation', ['episode', 'retry'])
def test_reports_repeatable_read_does_not_block_writer(mutation):
    factory = session_factory(os.environ['TEST_DATABASE_URL']); engine = factory.kw['bind']
    with factory() as s:
        u = User(username='report-'+uuid.uuid4().hex, password_hash='unused')
        p = Pool(name='report-'+uuid.uuid4().hex, gpu_model='virtual'); s.add_all([u,p]); s.flush()
        ids, jobs, runs = [], [], []
        for i in range(2):
            b = EvaluationBatch(owner_id=u.id, title='snapshot'); s.add(b); s.flush(); ids.append(b.id)
            j = Job(owner_id=u.id, pool_id=p.id, batch_id=b.id, case_id='same', title='snapshot',
                    benchmark='cs-client', model_ref='test', episodes=1, status='SUCCEEDED',
                    client_spec={'task_name':'task','seed':0,'endpoint':'http://server',
                                 'template':{'name':'client','version':'1','image':'test','episode_reporting':True}})
            s.add(j);s.flush();jobs.append(j.id)
            r=Run(job_id=j.id, attempt=1, status='SUCCEEDED');s.add(r);s.flush();runs.append(r.id)
            s.add(EpisodeRecord(run_id=r.id, episode_id=0, seed=0, started={}, result={'status':'COMPLETED','success':True}))
        s.commit();user=SimpleNamespace(id=u.id,role='user')
    triggered=False; statements=[]
    def writer():
        with factory() as s:
            s.execute(text("SET LOCAL lock_timeout='2s'"))
            for jid,rid in zip(jobs,runs):
                if mutation=='retry':
                    s.get(Job,jid).status='PENDING';s.add(Run(job_id=jid,attempt=2,status='PENDING'))
                else:
                    s.get(EpisodeRecord,(rid,0)).result={'status':'COMPLETED','success':False}
            s.commit()
    def interleave(conn,cursor,statement,params,context,many):
        nonlocal triggered
        statements.append(statement)
        if not triggered and statement.startswith('SELECT') and 'evaluation_batches' in statement:
            triggered=True
            assert conn.exec_driver_sql('SHOW transaction_isolation').scalar()=='repeatable read'
            assert conn.exec_driver_sql('SHOW transaction_read_only').scalar()=='on'
            with ThreadPoolExecutor(max_workers=1) as ex:ex.submit(writer).result(timeout=5)
    event.listen(engine,'after_cursor_execute',interleave)
    try: before=read_reports(engine,ids,user)
    finally:event.remove(engine,'after_cursor_execute',interleave)
    assert triggered
    assert compare(*before)['comparable']
    assert all(r['summary']['success_rate']==1 and r['cases'][0]['attempt']==1 for r in before)
    assert not any('FOR UPDATE' in q.upper() or 'ADVISORY' in q.upper() for q in statements)
    after=read_reports(engine,ids,user)
    if mutation=='retry':
        assert all(r['cases'][0]['attempt']==2 and r['summary']['success_rate'] is None for r in after)
    else:assert all(r['summary']['success_rate']==0 for r in after)
