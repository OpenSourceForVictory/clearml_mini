import uuid
from datetime import timedelta
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Run,LogReadRequest,now


def setup(env):
    main,c,auth=env;token,slots=node(env);job=submit(env);task=claim(env,token,slots[0])
    registration=dict(scope='episode',episode_id=1,generation=str(uuid.uuid4()),root_path='/old/runs/'+task['execution_id']+'/logs')
    route='/api/v1/worker/executions/'+task['execution_id']+'/log-catalog'
    r=c.post(route,headers=token,json=registration);assert r.status_code==200,r.text
    return main,c,auth,token,job,task,registration,r.json(),route


def test_log_read_terminal_auth_and_idempotent_response(env):
    main,c,auth,token,job,task,registration,catalog,route=setup(env)
    base=f'/api/v1/jobs/{job}/attempts/{task["run_id"]}'
    with main.SessionLocal() as s:
        run=s.get(Run,task['run_id']);run.status='FAILED';run.lease_expires_at=now()-timedelta(seconds=1);s.commit()
    assert c.post(route,headers=token,json={**registration,'status':'closed'}).status_code==200
    assert c.post(route,headers=token,json={**registration,'episode_id':2}).status_code==409
    assert c.post(route,headers=token,json={**registration,'root_path':'/other'}).status_code==409
    assert c.get(base+'/log-catalog',headers=auth['bob']).status_code==404
    response=c.post(base+'/log-reads',headers=auth['alice'],json={'log_id':catalog['id']});assert response.status_code==202,response.text
    access=response.json()['read_id']
    admin=c.post(base+'/log-reads',headers=auth['admin'],json={'log_id':catalog['id']}).json()['read_id']
    assert access!=admin
    assert c.get('/api/v1/log-reads/'+access,headers=auth['admin']).status_code==404
    claimed=c.post('/api/v1/worker/log-reads/claim',headers=token).json()['requests'];assert len(claimed)==1
    request=claimed[0];payload={'claim':request['claim'],'generation':request['generation'],'status':'ready','page':{'text':'hello'}}
    endpoint='/api/v1/worker/log-reads/'+request['id']+'/reply'
    for _ in range(2):assert c.post(endpoint,headers=token,json=payload).status_code==200
    assert c.post(endpoint,headers=token,json={**payload,'claim':'x'*24}).status_code==409
    assert c.get('/api/v1/log-reads/'+access,headers=auth['alice']).json()['page']=={'text':'hello'}
    assert c.get('/api/v1/log-reads/'+admin,headers=auth['admin']).json()['page']=={'text':'hello'}
    with main.SessionLocal() as s:
        s.get(LogReadRequest,request['id']).expires_at=now()-timedelta(seconds=1);s.commit()
    assert c.get('/api/v1/log-reads/'+access,headers=auth['alice']).json()['status']=='expired'
    assert c.post(endpoint,headers=token,json=payload).status_code==409


def test_read_admission_bounds_cross_node_and_cleaned_tombstone(env):
    main,c,auth,token,job,task,registration,catalog,route=setup(env)
    other,_=node(env,'other')
    assert c.post(route,headers=other,json=registration).status_code==404
    base=f'/api/v1/jobs/{job}/attempts/{task["run_id"]}/log-reads'
    for i in range(8):assert c.post(base,headers=auth['alice'],json={'log_id':catalog['id'],'until':i}).status_code==202
    assert c.post(base,headers=auth['alice'],json={'log_id':catalog['id'],'until':9}).status_code==429
    requests=c.post('/api/v1/worker/log-reads/claim',headers=token).json()['requests'];assert len(requests)==2
    assert not c.post('/api/v1/worker/log-reads/claim',headers=token).json()['requests']
    assert not c.post('/api/v1/worker/log-reads/claim',headers=other).json()['requests']
    assert c.post(route,headers=token,json={**registration,'status':'cleaned'}).status_code==200
    assert c.post(route,headers=token,json=registration).status_code==409
    assert c.post(route,headers=token,json={**registration,'episode_id':4}).status_code==400
    assert c.post(route,headers=token,json={**registration,'root_path':'/bad/../root'}).status_code==400
    assert c.post(base,headers=auth['alice'],json={'log_id':catalog['id'],'limit_bytes':65537}).status_code==400
    assert c.post('/api/v1/worker/log-reads/'+requests[0]['id']+'/reply',headers=token,json={'claim':requests[0]['claim'],'generation':registration['generation'],'status':'ready','page':{'text':'x'*65536}}).status_code==400
