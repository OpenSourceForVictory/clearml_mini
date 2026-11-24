"""Real PostgreSQL pool exhaustion; no production databases or workers."""
import time
import pytest
from backend.tests.test_db_budget import online, pg
from backend.tests.test_services import setup
from backend.app.core import Pool, Job, Run


@pytest.mark.parametrize('operation',['retry','retry-failed','service-start'])
def test_controls_and_heartbeat_survive_ordinary_pool_exhaustion(online,operation):
    main,c,worker=online
    auth={'Authorization':'Bearer '+main.login_token('u',int(time.time()))['access_token']}
    with main.SessionLocal() as s:
        s.get(Job,'j').status='FAILED';r=s.get(Run,'r');r.status='FAILED';r.cleanup_confirmed=True
        s.add(Pool(id='mock',name='mock',gpu_model='virtual'));s.commit()
    if operation=='service-start':
        _,_,body=setup((main,c,{'admin':auth}))
        response=c.post('/api/v1/admin/services',headers=auth,json=body)
        assert response.status_code==201,response.text
        item=response.json();path='/api/v1/admin/services/'+item['id']+'/control'
        assert c.post(path,headers=auth,json={'action':'stop','expected_revision':0}).status_code==200
        body={'action':'start','expected_revision':1}
    elif operation=='retry':path='/api/v1/jobs/j/retry';body=None
    else:path='/api/v1/batches/b/retry-failed';body={'items':[{'job_id':'j','expected_run_id':'r'}]}
    ordinary=main.SessionLocal.kw['bind']
    with ordinary.connect():
        logs=c.post('/api/v1/worker/executions/e/logs-v2',headers=worker,json={'sequence':0,'content':'pool pressure'})
        assert logs.status_code==503 and logs.json()['reason']=='connection_pool'
        response=c.post(path,headers=auth,json=body)
        assert response.status_code in (200,202),response.text
        heartbeat=c.post('/api/v1/worker/heartbeat',headers=worker,json={'slot_id':'s'})
        assert heartbeat.status_code==200,heartbeat.text
        replay=c.post(path,headers=auth,json=body)
        if operation=='retry-failed':assert replay.json()['retried']==0
        else:assert replay.status_code==409,replay.text
    assert main.ControlSessionLocal.kw['bind'].pool.checkedout()==0


def test_retry_lock_contention_rolls_back_then_recovers(online):
    from sqlalchemy import text,select
    from backend.app.scheduling import LOCK_ID,tick
    main,c,_=online
    auth={'Authorization':'Bearer '+main.login_token('u',int(time.time()))['access_token']}
    with main.SessionLocal() as s:
        s.get(Job,'j').status='FAILED';r=s.get(Run,'r');r.status='FAILED';r.cleanup_confirmed=True;s.commit()
    with main.SchedulerSessionLocal() as holder:
        holder.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':LOCK_ID})
        response=c.post('/api/v1/jobs/j/retry',headers=auth)
        assert response.status_code==503 and response.json()['reason']=='lock_timeout',response.text
        assert tick(main.ControlSessionLocal)==0
    with main.ControlSessionLocal() as s:
        assert s.get(Job,'j').status=='FAILED'
        assert len(list(s.scalars(select(Run).where(Run.job_id=='j'))))==1
    assert c.post('/api/v1/jobs/j/retry',headers=auth).status_code==202
    assert main.ControlSessionLocal.kw['bind'].pool.checkedout()==0
