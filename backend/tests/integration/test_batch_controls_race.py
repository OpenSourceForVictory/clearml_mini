"""Control ordering requires PostgreSQL advisory locks, not SQLite threads."""
import os,uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.app.core import User,Pool,Run,Job,EvaluationBatch,session_factory
from backend.app.scheduling import tick,transition

@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='dedicated PostgreSQL required')
@pytest.mark.parametrize('race',['pause_claim','cancel_claim','pause_tick','cancel_retry','opposite'])
def test_control_ordering(env,monkeypatch,race):
    main,_,_=env;factory=session_factory(os.environ['TEST_DATABASE_URL']);monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        u=User(username='controls-'+suffix,password_hash='unused',role='admin');p=Pool(name='controls-'+suffix,gpu_model='virtual');s.add_all([u,p]);s.commit();uid,pid=u.id,p.id
    c=TestClient(main.app);auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    def post(path,body=None,headers=auth):return c.post('/api/v1/'+path,headers=headers,json=body)
    w=post('admin/worker-credentials',{'node_name':'controls-'+suffix}).json();wa={'Authorization':'Bearer '+w['token']}
    slot=post('worker/register',{'pool_id':pid,'gpu_index':0,'gpu_uuid':'mock:'+suffix,'gpu_model':'virtual','vram_gb':0,'protocol_version':3,'client_images':['test:1']},wa).json()['slot_id']
    t=post('admin/client-templates',{'name':'controls-'+suffix,'version':'1','image':'test:1','command':['true'],'protocols':['http']}).json()['id']
    b=post('batches',{'title':'race','pool_id':pid,'client':{'template_id':t,'endpoint':'http://inference:8080'},'cases':[{'case_id':'a','task_name':'a','episodes':1}]}).json();job=b['cases'][0]['job_id']
    if race.endswith('claim'):assert tick(factory)==1
    if race=='cancel_retry':
        with factory() as s:transition(s,s.get(Job,job).runs[-1],'FAILED');s.commit()
    barrier=Barrier(2)
    def first():
        barrier.wait();return post('batches/'+b['id']+'/control',{'action':'cancel' if race.startswith('cancel') else 'pause','expected_revision':0})
    def second():
        barrier.wait()
        if race.endswith('claim'):return post('worker/slots/'+slot+'/claim',headers=wa)
        if race=='pause_tick':return tick(factory)
        if race=='cancel_retry':return post('jobs/'+job+'/retry')
        return post('batches/'+b['id']+'/control',{'action':'resume','expected_revision':0})
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            a=ex.submit(first);z=ex.submit(second);a,z=a.result(),z.result()
        if race=='opposite':assert sorted([a.status_code,z.status_code])==[200,409]
        else:assert a.status_code==200,a.text
        with factory() as s:
            batch=s.get(EvaluationBatch,b['id']);j=s.get(Job,job);runs=j.runs
            assert batch.control_revision==1
            assert len(runs)<=2 and len({r.attempt for r in runs})==len(runs)
            if race=='pause_claim':
                assert j.status in ('PENDING','STARTING')
                assert (z.json()['task'] is None)==(j.status=='PENDING')
                if j.status=='STARTING':assert not runs[-1].cleanup_confirmed
            if race=='cancel_claim':
                assert j.status in ('CANCELLED','CANCEL_REQUESTED')
                assert (z.json()['task'] is None)==(j.status=='CANCELLED')
                if j.status=='CANCEL_REQUESTED':assert not runs[-1].cleanup_confirmed and runs[-1].slot_id==slot
            if race=='pause_tick':assert j.status=='PENDING' and runs[-1].slot_id is None
            if race=='cancel_retry':assert z.status_code==202 and j.status in ('CANCELLED','PENDING')
        if race!='opposite':
            assert tick(factory)==0
            assert post('batches/'+b['id']+'/control',{'action':'cancel','expected_revision':0}).status_code==409
    finally:
        with factory() as s:
            for r in s.scalars(select(Run).where(Run.job_id==job)):
                transition(s,r,'CANCELLED');r.cleanup_confirmed=True
            s.commit()
