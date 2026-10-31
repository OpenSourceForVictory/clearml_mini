from sqlalchemy import event
from backend.tests.test_execution import env
from backend.tests.test_preflight import setup, counts
from backend.app.core import Job, Run, EpisodeRecord, Pool, User, ClientTemplate, Slot
from backend.app.scheduling import tick, transition


def batch(env):
    main,c,auth=env
    body,sid=setup(env)
    response=c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert response.status_code==201,response.text
    return response.json(),sid


def get(env,b,owner='alice'):
    _,c,auth=env
    r=c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth[owner])
    assert r.status_code==200,r.text
    return r.json()


def fail(env,b,cleanup=True):
    main,_,_=env
    with main.SessionLocal() as s:
        for case in b['cases']:
            run=s.get(Run,case['run_id']);transition(s,run,'FAILED','test execution exception');run.cleanup_confirmed=cleanup
        s.commit()


def selection(b):
    return {'items':[{'job_id':c['job_id'],'expected_run_id':c['run_id']} for c in b['cases']]}


def test_monitor_dynamic_reasons_snapshot_and_permissions(env):
    main,c,auth=env;b,sid=batch(env);before=counts(main)
    assert all(x['waiting']['status']=='awaiting_scheduler' for x in get(env,b)['cases'])
    assert counts(main)==before
    assert c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth['bob']).status_code==404
    with main.SessionLocal() as s:
        j=s.get(Job,b['cases'][0]['job_id'])
        s.get(ClientTemplate,j.client_spec['template']['id']).enabled=False
        s.get(Slot,sid).client_images=[];s.commit()
    assert 'client_image' in get(env,b)['cases'][0]['reason_codes']
    with main.SessionLocal() as s:s.get(Slot,sid).client_images=['approved:cs'];s.commit()
    assert tick(main.SessionLocal)==1
    m=get(env,b)
    for case in m['cases']:
        if case['status']=='RESERVED':assert case['waiting'] is None and case['node_name']=='node'
        else:assert 'batch_concurrency' in case['reason_codes'] and 'client_image' not in case['reason_codes']
    with main.SessionLocal() as s:
        s.get(Pool,'mock').enabled=False;s.get(User,'alice').active=False;s.commit()
    queued=next(x for x in get(env,b,'admin')['cases'] if x['status']=='PENDING')
    assert {'pool_disabled','owner_disabled'}<=set(queued['reason_codes'])


def test_summary_latest_attempt_weighted_results(env):
    main,c,auth=env;b,_=batch(env)
    with main.SessionLocal() as s:
        for n,case in enumerate(b['cases']):
            job=s.get(Job,case['job_id']);job.client_spec={**job.client_spec,'template':{**job.client_spec['template'],'episode_reporting':True}}
            run=s.get(Run,case['run_id']);transition(s,run,'FAILED' if n else 'SUCCEEDED')
            for i in range(n+1):s.add(EpisodeRecord(run_id=run.id,episode_id=i+1,seed=0,started={},result={'status':'COMPLETED','success':n==0}))
        s.commit()
    m=get(env,b);assert m['summary']['completed_episodes']==3
    assert m['summary']['success_rate']==1/3 and m['summary']['goal_failures']==2
    assert m['summary']['execution_failures']==1
    failed=b['cases'][1]
    r=c.post('/api/v1/jobs/'+failed['job_id']+'/retry',headers=auth['alice']);assert r.status_code==202
    m=get(env,b);assert m['summary']['completed_episodes']==1 and m['summary']['success_rate']==1
    old=c.get(f"/api/v1/jobs/{failed['job_id']}/attempts/{failed['run_id']}/episodes",headers=auth['alice'])
    assert old.json()['progress']['completed']==2


def test_no_reporting_and_query_budget(env):
    main,c,auth=env;body,_=setup(env)
    body['cases']=[{**body['cases'][0],'case_id':str(i)} for i in range(100)]
    b=c.post('/api/v1/batches',headers=auth['alice'],json=body).json();queries=[]
    engine=main.SessionLocal.kw['bind']
    def trace(*args):queries.append(args[2])
    event.listen(engine,'before_cursor_execute',trace)
    try:m=get(env,b)
    finally:event.remove(engine,'before_cursor_execute',trace)
    assert len(queries)<=15, len(queries)
    assert m['summary']['unreported_tasks']==100 and m['summary']['success_rate'] is None
    assert m['summary']['completed_episodes']==0


def test_bulk_retry_partial_cleanup_replay_and_history(env):
    main,c,auth=env;b,_=batch(env);fail(env,b)
    with main.SessionLocal() as s:s.get(Run,b['cases'][1]['run_id']).cleanup_confirmed=False;s.commit()
    path='/api/v1/batches/'+b['id']+'/retry-failed';body=selection(b)
    before={x['job_id']:c.get('/api/v1/jobs/'+x['job_id'],headers=auth['alice']).json()['client_spec'] for x in b['cases']}
    r=c.post(path,headers=auth['alice'],json=body);assert r.status_code==200,r.text
    assert r.json()['retried']==1 and r.json()['skipped']==1
    assert r.json()['items'][1]['code']=='cleanup_pending'
    retried=r.json()['items'][0]
    with main.SessionLocal() as s:transition(s,s.get(Run,retried['run_id']),'FAILED');s.commit()
    replay=c.post(path,headers=auth['alice'],json=body).json()
    assert replay['retried']==0 and replay['items'][0]['code']=='attempt_changed'
    for case in b['cases']:
        detail=c.get('/api/v1/jobs/'+case['job_id'],headers=auth['alice']).json()
        assert detail['client_spec']==before[case['job_id']]
        assert detail['attempts'][0]['error']=='test execution exception'
    assert c.post(path,headers=auth['bob'],json=body).status_code==404


def test_bulk_retry_atomic_membership_validation_and_quota(env,monkeypatch):
    main,c,auth=env;b,_=batch(env);fail(env,b);path='/api/v1/batches/'+b['id']+'/retry-failed'
    invalid=selection(b);invalid['items'].append({'job_id':'foreign','expected_run_id':'hidden'})
    before=counts(main)
    assert c.post(path,headers=auth['alice'],json=invalid).status_code==404
    assert counts(main)==before
    monkeypatch.setenv('MAX_BATCH_BACKLOG_PER_USER','1')
    r=c.post(path,headers=auth['alice'],json=selection(b)).json()
    assert r['retried']==1 and r['items'][1]['code']=='backlog_limit'


def test_bulk_does_not_retry_goal_failure_cancelled_or_running(env):
    main,c,auth=env;b,_=batch(env)
    with main.SessionLocal() as s:
        transition(s,s.get(Run,b['cases'][0]['run_id']),'SUCCEEDED')
        transition(s,s.get(Run,b['cases'][1]['run_id']),'CANCELLED');s.commit()
    r=c.post('/api/v1/batches/'+b['id']+'/retry-failed',headers=auth['alice'],json=selection(b))
    assert r.json()['retried']==0 and all(x['code']=='not_retryable' for x in r.json()['items'])
