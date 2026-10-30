import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from backend.app.core import User, ClientTemplate, session_factory
from backend.tests.test_execution import env

@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='dedicated PostgreSQL required')
def test_publish_and_edit_are_serialized(env,monkeypatch):
    main,_,_=env
    factory=session_factory(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        u=User(username='suite-race-'+suffix,password_hash='unused')
        t=ClientTemplate(name='suite-race-'+suffix,version='1',image='test:1',command=['true'],protocols=['http'],enabled=True)
        s.add_all([u,t]);s.commit();uid,tid=u.id,t.id
    auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    c=TestClient(main.app)
    body={'name':'race','version':'1','template_id':tid,'status':'draft','cases':[{'case_id':'a','task_name':'a','episodes':1}]}
    x=c.post('/api/v1/suites',headers=auth,json=body).json()
    path='/api/v1/suites/'+x['id'];barrier=Barrier(2)
    def edit():
        barrier.wait()
        return c.put(path,headers=auth,json={**body,'expected_revision':0,'description':'edited'})
    def publish():
        barrier.wait()
        return c.post(path+'/actions',headers=auth,json={'action':'publish','expected_revision':0})
    with ThreadPoolExecutor(max_workers=2) as executor:
        a,b=executor.submit(edit),executor.submit(publish)
        assert sorted([a.result().status_code,b.result().status_code])==[200,409]
    assert c.get(path,headers=auth).json()['revision']==1
