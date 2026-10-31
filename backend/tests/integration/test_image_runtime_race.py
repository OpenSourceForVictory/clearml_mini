import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from backend.tests.test_execution import env
from backend.app.core import User, Worker, session_factory


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='dedicated PostgreSQL required')
def test_concurrent_node_configuration_has_one_winner(env,monkeypatch):
    main,_,_=env
    factory=session_factory(os.environ['TEST_DATABASE_URL']);monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        user=User(username='runtime-race-'+suffix,password_hash='unused',role='admin')
        worker=Worker(node_name='runtime-race-'+suffix,token_hash=suffix)
        s.add_all([user,worker]);s.commit();uid,wid=user.id,worker.id
    auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    c=TestClient(main.app);barrier=Barrier(2)
    def save(image):
        barrier.wait()
        return c.put('/api/v1/admin/nodes/'+wid+'/runtime',headers=auth,json={'expected_revision':0,'config':{'images':[image],'assets':{}}})
    with ThreadPoolExecutor(max_workers=2) as executor:
        a,b=executor.submit(save,'a:1'),executor.submit(save,'b:1')
        assert sorted([a.result().status_code,b.result().status_code])==[200,409]
    assert c.get('/api/v1/admin/nodes/'+wid+'/runtime',headers=auth).json()['revision']==1


"""Use the dedicated migrated TEST_DATABASE_URL database."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.core import User, Run, Job, Worker, Slot, session_factory
from backend.app.scheduling import tick
from backend.tests.test_execution import env
from backend.tests.test_storage import CONFIG


@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='dedicated PostgreSQL required')
def test_concurrent_runtime_save_and_claim(env, monkeypatch):
    main,_,_=env
    factory=session_factory(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        u=User(username='runtime-claim-race-'+suffix,role='admin',password_hash='unused');s.add(u);s.commit();uid=u.id
    auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    c=TestClient(main.app)
    pool=c.post('/api/v1/admin/pools',headers=auth,json={'name':'runtime-claim-race-'+suffix,'gpu_model':'virtual'}).json()['id']
    w=c.post('/api/v1/admin/worker-credentials',headers=auth,json={'node_name':'runtime-claim-race-'+suffix}).json()
    token={'Authorization':'Bearer '+w['token']}
    slot=c.post('/api/v1/worker/register',headers=token,json={'pool_id':pool,'gpu_index':0,'gpu_uuid':'mock:'+suffix,'gpu_model':'virtual','vram_gb':0,'protocol_version':3}).json()['slot_id']
    job=c.post('/api/v1/jobs',headers=auth,json={'title':'race','benchmark':'mock','model_ref':'test','pool_id':pool,'episodes':1}).json()['id']
    assert tick(factory)==1
    barrier=Barrier(4)
    path='/api/v1/admin/nodes/'+w['id']+'/runtime'
    def save():
        barrier.wait()
        return c.put(path,headers=auth,json={'expected_revision':0,'config':{'images':['approved:1'],'assets':{}}})
    def claim():
        barrier.wait()
        return c.post('/api/v1/worker/slots/'+slot+'/claim',headers=token)
    def storage_save():
        barrier.wait()
        return c.put('/api/v1/admin/nodes/'+w['id']+'/storage', headers=auth,
                     json={'expected_revision':0,'config':CONFIG})
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            a,b,d=executor.submit(save),executor.submit(save),executor.submit(claim)
            storage=executor.submit(storage_save)
            assert storage.result().status_code==200
            saves=[a.result(),b.result()];claimed=d.result().json()['task']
        assert sorted(x.status_code for x in saves)==[200,409]
        with factory() as s:
            run=s.scalar(select(Run).where(Run.job_id==job))
            assert s.get(Worker,w['id']).runtime_revision==1
            if claimed:
                assert run.status=='STARTING' and not run.cleanup_confirmed
                assert run.execution_id==claimed['execution_id'] and run.reserved_cpu>0
            else:
                assert run.status=='PENDING' and run.slot_id is None and run.reserved_cpu==0
        assert tick(factory)==0
    finally:
        c.post('/api/v1/jobs/'+job+'/cancel',headers=auth)
        with factory() as s:
            run=s.scalar(select(Run).where(Run.job_id==job));execution=run.execution_id
        if execution: c.post('/api/v1/worker/executions/'+execution+'/cleanup',headers=token)
        c.patch('/api/v1/admin/nodes/'+w['id'],headers=auth,json={'enabled':False})
