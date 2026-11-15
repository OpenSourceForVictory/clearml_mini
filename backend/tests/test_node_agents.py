import copy
import uuid
from datetime import timedelta
import pytest
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Worker,Run,now
from sqlalchemy import select
from backend.app.node_agents import admission


def setup(env):
    main,c,a=env;t,slots=node(env)
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    r=c.post('/api/v1/agent/register',headers=t,json={'boot_id':uuid.uuid4().hex,'version':'1.0.0'})
    assert r.status_code==200,r.text
    return t,slots,n['id'],r.json()['session']


def beat(c,t,session,seq=1,revision=0,**kw):
    return c.post('/api/v1/agent/heartbeat',headers=t,json={'session':session,'sequence':seq,'applied_revision':revision,'config_status':'applied',**kw})


def test_identity_permissions_and_stale_session(env):
    _,c,a=env;t,_,identity,session=setup(env)
    path='/api/v1/admin/nodes/'+identity+'/agent'
    assert c.get(path,headers=a['alice']).status_code==403
    assert c.post('/api/v1/agent/register',headers=a['admin'],json={'boot_id':uuid.uuid4().hex,'version':'1'}).status_code==401
    assert beat(c,t,session).status_code==200
    c.post('/api/v1/agent/register',headers=t,json={'boot_id':uuid.uuid4().hex,'version':'1'})
    assert beat(c,t,session,2).status_code==409
    assert 'session' not in c.get(path,headers=a['admin']).json()


def test_sequences_events_and_bounded_payload(env):
    _,c,a=env;t,_,identity,session=setup(env)
    assert beat(c,t,session,2,events=[{'sequence':1,'code':'started'}]).json()['event_ack']==1
    assert beat(c,t,session,1,config_status='failed').status_code==200
    r=c.get('/api/v1/admin/nodes/'+identity+'/agent',headers=a['admin']).json()
    assert r['sequence']==2 and r['config_status']=='applied' and len(r['events'])==1
    assert beat(c,t,session,3,events=[{'sequence':2,'code':'password'}]).status_code==400
    assert beat(c,t,session,3,events=[{'sequence':i,'code':'started'} for i in range(1,66)]).status_code==400
    assert beat(c,t,session,3,revision=99).status_code==409


def test_drain_keeps_execution_lease_and_requires_cleanup(env):
    main,c,a=env;t,slots,identity,session=setup(env);beat(c,t,session)
    submit(env);task=claim(env,t,slots[0]);assert task
    path='/api/v1/admin/nodes/'+identity+'/agent'
    r=c.put(path,headers=a['admin'],json={'expected_revision':0,'programs':'stopped'})
    assert r.status_code==200,r.text
    permit={'session':session,'revision':1}
    assert not c.post('/api/v1/agent/stop-permit',headers=t,json=permit).json()['allowed']
    ex=task['execution_id']
    assert c.post('/api/v1/worker/heartbeat',headers=t,json={'slot_id':slots[0],'execution_id':ex}).status_code==200
    assert c.post('/api/v1/worker/executions/'+ex+'/complete',headers=t,json={'status':'FAILED','error':'test'}).status_code==200
    # Completion normally confirms the process is stopped. Simulate a terminal
    # execution whose cleanup was interrupted to exercise the independent barrier.
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==ex));run.cleanup_confirmed=False;s.commit()
    assert not c.post('/api/v1/agent/stop-permit',headers=t,json=permit).json()['allowed']
    assert c.post('/api/v1/worker/executions/'+ex+'/cleanup',headers=t).status_code==200
    assert c.post('/api/v1/agent/stop-permit',headers=t,json=permit).json()['allowed']
    assert c.put(path,headers=a['admin'],json={'expected_revision':1,'programs':'running'}).status_code==409
    beat(c,t,session,2,1)
    assert c.put(path,headers=a['admin'],json={'expected_revision':1,'programs':'running'}).status_code==200
    assert not c.post('/api/v1/agent/stop-permit',headers=t,json=permit).json()['allowed']
    submit(env);assert claim(env,t,slots[0]) is None
    beat(c,t,session,3,2);assert claim(env,t,slots[0])


@pytest.mark.parametrize('body',[{'heartbeat_seconds':1},{'sample_seconds':121},{'programs':'reboot'},{'unit':'sshd.service'},{'expected_revision':99}])
def test_configuration_validation_and_revision(env,body):
    _,c,a=env;_,_,identity,_=setup(env)
    path='/api/v1/admin/nodes/'+identity+'/agent'
    r=c.put(path,headers=a['admin'],json={'expected_revision':0,**body})
    assert r.status_code in (409,400)
    assert c.get(path,headers=a['admin']).json()['revision']==0


def test_connection_merge_and_offline_admission(env):
    main,c,a=env;t,_,identity,session=setup(env)
    with main.SessionLocal() as s:
        w=s.get(Worker,identity);w.connection={**w.connection,'enrollment':{'state':'COMPLETE'}};s.commit()
    beat(c,t,session)
    with main.SessionLocal() as s:
        w=s.get(Worker,identity);assert w.connection['enrollment']['state']=='COMPLETE';assert admission(w)
        w.connection={**w.connection,'agent':{**w.connection['agent'],'seen_at':(now()-timedelta(seconds=46)).isoformat()}}
        assert not admission(w)
        w.connection=None;assert admission(w)


def test_two_gpu_queue_agent_heartbeat_does_not_renew_execution(env):
    from backend.app.scheduling import tick
    from backend.app.core import Slot
    main,c,a=env;t,slots=node(env,count=2)
    identity=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]['id']
    r=c.post('/api/v1/agent/register',headers=t,json={'boot_id':uuid.uuid4().hex,'version':'1'})
    session=r.json()['session'];beat(c,t,session)
    for _ in range(3):submit(env)
    first=claim(env,t,slots[0]);second=claim(env,t,slots[1]);assert first and second
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==first['execution_id']));deadline=run.lease_expires_at
    beat(c,t,session,2)
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==first['execution_id']));assert run.lease_expires_at==deadline
        assert s.scalar(select(Run).where(Run.status=='PENDING')) is not None
    path='/api/v1/admin/nodes/'+identity+'/agent'
    assert c.put(path,headers=a['admin'],json={'expected_revision':0,'programs':'stopped'}).status_code==200
    for task in (first,second):
        ex=task['execution_id'];assert c.post('/api/v1/worker/executions/'+ex+'/complete',headers=t,json={'status':'FAILED','error':'test finished'}).status_code==200
    assert tick(main.SessionLocal)==0
    assert c.post('/api/v1/agent/stop-permit',headers=t,json={'session':session,'revision':1}).json()['allowed']


@pytest.mark.parametrize('upgrade',['SUCCEEDED','CANCELLED',None])
def test_completed_upgrade_does_not_block_later_stop(env,upgrade):
    main,c,a=env;t,_,identity,session=setup(env);beat(c,t,session)
    with main.SessionLocal() as s:s.get(Worker,identity).upgrade_state=upgrade;s.commit()
    r=c.put('/api/v1/admin/nodes/'+identity+'/agent',headers=a['admin'],json={'expected_revision':0,'programs':'stopped'})
    assert r.status_code==200
    assert c.post('/api/v1/agent/stop-permit',headers=t,json={'session':session,'revision':1}).json()['allowed']


def test_session_never_exposed_in_machine_listing(env):
    _,c,a=env;_,_,_,session=setup(env)
    payload=c.get('/api/v1/admin/nodes',headers=a['admin']).json()
    assert session not in str(payload)


def test_preflight_explains_agent_admission():
    from backend.app.preflight import reason
    value=reason('node_agent_not_ready')
    assert value['category']=='environment' and 'Agent' in value['message'] and '管理员' in value['action']


def test_offline_agent_blocks_new_claim_but_not_existing_execution_heartbeat(env):
    main,c,a=env;t,slots,identity,session=setup(env);beat(c,t,session)
    submit(env);task=claim(env,t,slots[0]);assert task
    with main.SessionLocal() as s:
        w=s.get(Worker,identity)
        w.connection={**w.connection,'agent':{**w.connection['agent'],'seen_at':(now()-timedelta(seconds=46)).isoformat()}}
        s.commit()
    assert c.get('/api/v1/admin/nodes/'+identity+'/agent',headers=a['admin']).json()['status']=='offline'
    ex=task['execution_id']
    assert c.post('/api/v1/worker/heartbeat',headers=t,json={'slot_id':slots[0],'execution_id':ex}).status_code==200
    assert c.post('/api/v1/worker/executions/'+ex+'/complete',headers=t,json={'status':'FAILED','error':'test finished'}).status_code==200
    submit(env);assert claim(env,t,slots[0]) is None
    beat(c,t,session,2);assert claim(env,t,slots[0])


def test_duplicate_heartbeat_does_not_revive_expired_agent(env):
    main,c,a=env;t,_,identity,session=setup(env);beat(c,t,session,5)
    with main.SessionLocal() as s:
        w=s.get(Worker,identity)
        old=(now()-timedelta(seconds=46)).isoformat()
        w.connection={**w.connection,'agent':{**w.connection['agent'],'seen_at':old}};s.commit()
    assert beat(c,t,session,5).status_code==200
    r=c.get('/api/v1/admin/nodes/'+identity+'/agent',headers=a['admin']).json()
    assert r['status']=='offline' and r['seen_at']==old
    assert beat(c,t,session,6).status_code==200
    assert c.get('/api/v1/admin/nodes/'+identity+'/agent',headers=a['admin']).json()['status']=='online'
