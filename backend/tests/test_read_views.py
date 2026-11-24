"""Read projections must agree with the existing UI contract, without N+1 SQL."""
from contextlib import contextmanager
from sqlalchemy import event, select
from backend.tests.test_execution import env
from backend.tests.test_monitoring import batch, fail
from backend.app.core import Job, Run, EpisodeRecord, Worker, Slot, EvaluationBatch
from backend.app.administration import node_view, node_views
from backend.app.batches import batch_view, batch_summaries
from backend.app.read_views import latest_jobs


@contextmanager
def queries(engine):
    statements=[]
    def trace(conn,cursor,statement,*args):statements.append(statement)
    event.listen(engine,'before_cursor_execute',trace)
    try:yield statements
    finally:event.remove(engine,'before_cursor_execute',trace)


def test_summary_contract_and_retry(env):
    main,c,auth=env;b,_=batch(env)
    with main.SessionLocal() as s:
        for n,item in enumerate(b['cases']):
            j=s.get(Job,item['job_id']);r=s.get(Run,item['run_id'])
            j.client_spec={**j.client_spec,'template':{**j.client_spec['template'], 'execution_mode':'host','host_runtime':{'episode_launch':'per_episode'},'episode_reporting':True}}
            r.status=j.status='FAILED';r.exit_code=0
            s.add(EpisodeRecord(run_id=r.id,episode_id=1,seed=0,started={},result={'status':'ERROR','error':'direct protocol episode must emit exactly one final result','exit_code':0,'metrics':{'large':'x'*10000}}))
        s.commit()
    def compare():
        full=c.get('/api/v1/batches',headers=auth['alice']).json()
        brief=c.get('/api/v1/batches?view=summary',headers=auth['alice']).json()
        assert len(full)==len(brief)
        for a,b in zip(full,brief):
            assert 'cases' not in b
            for key in a.keys()-{'cases','timing'}:assert a[key]==b[key],key
    compare()
    assert c.post('/api/v1/jobs/'+b['cases'][0]['job_id']+'/retry',headers=auth['alice']).status_code==202
    compare()
    assert c.get('/api/v1/batches?view=summary',headers=auth['bob']).json()==[]
    assert c.get('/api/v1/batches?view=invalid',headers=auth['alice']).status_code==400
    assert c.get('/api/v1/batches?view=summary&page=2',headers=auth['alice']).json()==[]


def test_node_batching_matches_single_views(env):
    main,_,_=env
    with main.SessionLocal() as s:
        s.add_all([Worker(id='extra'+str(i),node_name='extra'+str(i),token_hash=f'{i:064x}') for i in range(30)])
        s.commit()
    with main.SessionLocal() as s:
        workers=list(s.scalars(select(Worker)))
        with queries(s.bind) as sql:actual=node_views(s,workers)
        assert len(sql)<=2
        assert actual==[node_view(s,w) for w in workers]
        assert node_views(s,[])==[]


def test_summary_batch_queries_and_latest_attempt_only(env):
    main,_,_=env;b,_=batch(env)
    fail(env,b)
    with main.SessionLocal() as s:
        j=s.get(Job,b['cases'][0]['job_id'])
        s.add(Run(job_id=j.id,attempt=2,status='PENDING'));s.commit()
    with main.SessionLocal() as s:
        with queries(s.bind) as sql:
            jobs=latest_jobs(s,[b['id']])
        assert len(sql)==1
        assert len(jobs[0].runs)==1 and jobs[0].runs[0].attempt==2
    with main.SessionLocal() as s:
        batches=list(s.scalars(select(EvaluationBatch)))
        with queries(s.bind) as sql:brief=batch_summaries(s,batches)
        assert len(sql)<=3
        assert not any('evaluation_events' in q for q in sql)
        assert brief and batch_summaries(s,[])==[]


def test_finished_monitor_skips_global_diagnostics(env,monkeypatch):
    main,c,auth=env;b,_=batch(env);fail(env,b)
    def forbidden(*args):raise AssertionError('finished batch loaded global diagnostics')
    monkeypatch.setattr('backend.app.monitoring.DiagnosticContext',forbidden)
    response=c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth['alice'])
    assert response.status_code==200,response.text
    assert all(x['waiting'] is None for x in response.json()['cases'])


def test_service_status_view_matches_full_without_episode_queries(env,monkeypatch):
    from backend.tests.test_orchestrations import setup,submit
    from backend.app.scheduling import tick
    main,c,auth=env
    *_,body=setup(env,monkeypatch)
    b=submit(env,body)
    for allocated in (False,True):
        if allocated:tick(main.SessionLocal)
        full=c.get('/api/v1/orchestrations/'+b['id'],headers=auth['alice']).json()
        with queries(main.SessionLocal.kw['bind']) as sql:
            response=c.get('/api/v1/orchestrations/'+b['id']+'?view_mode=status',headers=auth['alice'])
        assert response.status_code==200,response.text
        light=response.json()
        assert light['orchestration']['services']==full['orchestration']['services']
        assert light['orchestration']['definitions']==full['orchestration']['definitions']
        for a,bcase in zip(full['cases'],light['cases']):
            assert all(a[k]==v for k,v in bcase.items())
        assert not any('episode_records' in q or 'evaluation_events' in q for q in sql)
    assert c.get('/api/v1/orchestrations/'+b['id']+'?view_mode=status',headers=auth['bob']).status_code==404


def test_summary_modes_match_full(env):
    main,_,_=env;b,_=batch(env)
    modes=[{'task_type':'command'}, {'task_type':'command','integration_mode':'sdk-command'},
           {'integration_mode':'sdk-task-result'}, {'execution_mode':'host','host_runtime':{'episode_launch':'per_episode'}}, {}]
    for mode in modes:
        for status in ('PENDING','RUNNING','FAILED','SUCCEEDED','CANCELLED'):
            with main.SessionLocal() as s:
                j=s.get(Job,b['cases'][0]['job_id']);r=s.get(Run,b['cases'][0]['run_id'])
                j.client_spec={**j.client_spec,'template':mode};j.status=r.status=status
                r.exit_code=0 if status=='SUCCEEDED' else 1 if status=='FAILED' else None
                r.result_json={'kind':'task','success':False,'summary':'large payload'}
                s.commit()
            with main.SessionLocal() as s:
                batch_obj=s.get(EvaluationBatch,b['id']);full=batch_view(s,batch_obj)
                brief=batch_summaries(s,[batch_obj])[0]
                assert brief['outcomes']==full['outcomes']
                assert brief['total_episodes']==full['total_episodes']
                assert brief['status']==full['status']


def test_node_counts_multicard_active_and_unclean_terminal(env):
    from backend.tests.test_execution import node, submit
    main,c,auth=env
    _,slots=node(env,count=2)
    jobs=[submit(env) for _ in range(3)]
    with main.SessionLocal() as s:
        for index,jid in enumerate(jobs):
            r=s.scalar(select(Run).where(Run.job_id==jid));r.slot_id=slots[index%2]
            r.status='RUNNING' if index==0 else 'FAILED';r.cleanup_confirmed=index==2
        s.commit()
    response=c.get('/api/v1/admin/nodes',headers=auth['admin'])
    assert response.status_code==200
    assert response.json()[0]['gpu_count']==2
    assert response.json()[0]['held_executions']==2
    with main.SessionLocal() as s:
        w=s.scalar(select(Worker));w.enabled=False;s.commit()
    assert c.get('/api/v1/admin/nodes',headers=auth['admin']).json()[0]['held_executions']==2
