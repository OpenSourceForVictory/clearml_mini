"""Desired configuration and latest observations survive concurrent reports."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.core import Base,User,Worker,Run,session_factory
from backend.app.agent_state import record
from backend.tests.test_execution_logs_postgres import pg
from backend.app.scheduling import tick
from backend.tests.test_execution import env



@pytest.fixture(autouse=True)
def isolated_database(pg,monkeypatch):
    Base.metadata.create_all(pg[0])
    monkeypatch.setenv('TEST_DATABASE_URL',pg[1])


@pytest.mark.parametrize("resource_protocol", [0, 1])
def test_agent_configuration_heartbeat_and_claim_are_serialized(env,monkeypatch,resource_protocol):
    main,_,_=env;factory=session_factory(os.environ['TEST_DATABASE_URL']);monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        u=User(username='agent-race-'+suffix,role='admin',password_hash='unused');s.add(u);s.commit();uid=u.id
    auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')};c=TestClient(main.app)
    p=c.post('/api/v1/admin/pools',headers=auth,json={'name':'agent-'+suffix,'gpu_model':'virtual'}).json()['id']
    w=c.post('/api/v1/admin/worker-credentials',headers=auth,json={'node_name':'agent-'+suffix}).json();token={'Authorization':'Bearer '+w['token']}
    slot=c.post('/api/v1/worker/register',headers=token,json={'pool_id':p,'gpu_index':0,'gpu_uuid':'mock:'+suffix,'gpu_model':'virtual','vram_gb':0}).json()['slot_id']
    session=c.post('/api/v1/agent/register',headers=token,json={'boot_id':suffix,'version':'1','resource_protocol':resource_protocol}).json()['session']
    hb={'session':session,'sequence':1,'applied_revision':0,'config_status':'applied'}
    if resource_protocol:
        import time
        hb['samples']={'host':{'status':'ok','sampled_at':time.time(),'values':{'cpu_count':8,'memory_total_mb':32768,'memory_available_mb':20000}}}
    assert c.post('/api/v1/agent/heartbeat',headers=token,json=hb).status_code==200
    job=c.post('/api/v1/jobs',headers=auth,json={'title':'agent race','benchmark':'mock','model_ref':'test','pool_id':p,'episodes':1}).json()['id']
    assert tick(factory)==1
    barrier=Barrier(4);path='/api/v1/admin/nodes/'+w['id']+'/agent'
    def configure():barrier.wait();return c.put(path,headers=auth,json={'expected_revision':0,'programs':'stopped'})
    def heartbeat():barrier.wait();return c.post('/api/v1/agent/heartbeat',headers=token,json={**hb,'sequence':2})
    def claim():barrier.wait();return c.post('/api/v1/worker/slots/'+slot+'/claim',headers=token)
    with ThreadPoolExecutor(max_workers=4) as ex:
        a,b,h,k=[ex.submit(f) for f in (configure,configure,heartbeat,claim)]
        assert sorted([a.result().status_code,b.result().status_code])==[200,409]
        assert h.result().status_code==200;claimed=k.result().json()['task']
    state=c.get(path,headers=auth).json()
    assert state['revision']==1 and state['desired']['programs']=='stopped' and state['sequence']==2
    with factory() as s:
        run=s.scalar(select(Run).where(Run.job_id==job))
        assert (run.status=='STARTING' and not run.cleanup_confirmed) if claimed else run.status=='PENDING'
    permit=c.post('/api/v1/agent/stop-permit',headers=token,json={'session':session,'revision':1}).json()
    assert permit['allowed']==(claimed is None)
    if claimed:c.post('/api/v1/worker/executions/'+claimed['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'test teardown'})
    c.post('/api/v1/jobs/'+job+'/cancel',headers=auth)



@pytest.mark.parametrize('operation',['configure','storage','updater','register'])
def test_configuration_writer_does_not_wait_for_observation(env,monkeypatch,operation):
    """Hold a real observation transaction; configuration must commit independently."""
    from threading import Event
    from sqlalchemy import event
    from backend.app import node_agents
    from backend.tests.test_storage import CONFIG
    main,_,_=env
    factory=session_factory(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main,'SessionLocal',factory)
    suffix=uuid.uuid4().hex
    with factory() as s:
        user=User(username='snapshot-'+suffix,role='admin',password_hash='unused')
        s.add(user);s.commit();uid=user.id
    auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    c=TestClient(main.app)
    w=c.post('/api/v1/admin/worker-credentials',headers=auth,json={'node_name':'snapshot-'+suffix}).json()
    token={'Authorization':'Bearer '+w['token']}
    slot=c.post('/api/v1/worker/register',headers=token,json={'gpu_index':0,'gpu_uuid':'mock:'+suffix,'gpu_model':'virtual','vram_gb':0}).json()['slot_id']
    registration={'boot_id':suffix,'version':'1'}
    session=c.post('/api/v1/agent/register',headers=token,json=registration).json()['session']
    with factory() as s:
        worker=s.get(Worker,w['id'])
        worker.connection={**worker.connection,'enrollment':{'state':'INSTALLING','marker':'preserved'}}
        s.commit()
    body={'session':session,'sequence':2,'applied_revision':0,'config_status':'applied',
          'samples':{'host':{'status':'ok','sampled_at':1,'values':{'cpu_count':8}}}}
    entered,release=Event(),Event()
    original=node_agents.apply_resources
    def paused_apply(db,worker):
        original(db,worker)
        if worker.id==w['id']:
            entered.set()
            assert release.wait(10),'test did not release observation transaction'
    monkeypatch.setattr(node_agents,'apply_resources',paused_apply)
    engine=factory.kw['bind']
    prefix='/api/v1/admin/nodes/'+w['id']
    def mutate():
        if operation=='configure':return c.put(prefix+'/agent',headers=auth,json={'expected_revision':0,'programs':'stopped'})
        if operation=='storage':return c.put(prefix+'/storage/slots/'+slot,headers=auth,json={'expected_revision':0,'config':CONFIG})
        if operation=='updater':return c.post('/api/v1/worker/upgrades/poll',headers=token,json={'agent_digest':'a'*64})
        return c.post('/api/v1/agent/register',headers=token,json=registration)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            heartbeat=executor.submit(c.post,'/api/v1/agent/heartbeat',headers=token,json=body)
            try:
                assert entered.wait(5),'heartbeat did not acquire row lock'
                mutation=executor.submit(mutate)
                assert mutation.result(timeout=5).status_code==200
            finally:release.set()
            assert heartbeat.result(timeout=10).status_code==200
            assert mutation.result(timeout=10).status_code==200
    finally:
        release.set()
    with factory() as s:
        worker=s.get(Worker,w['id'])
        connection=worker.connection
        assert record(worker)['sequence']==2
        assert record(worker)['samples']==body['samples']
        assert connection['enrollment']['marker']=='preserved'
        if operation=='configure':assert connection['agent']['revision']==1
        if operation=='storage':assert connection['slot_storage_overrides'][slot]==CONFIG
        if operation=='updater':assert connection['enrollment']['agent_seen_digest']=='a'*64
    # A duplicate or stale report cannot replace the latest sample.
    assert c.post('/api/v1/agent/heartbeat',headers=token,json={**body,'sequence':1,'samples':{}}).status_code==200
    assert c.post('/api/v1/agent/heartbeat',headers=token,json={**body,'session':'f'*32}).status_code==409
    with factory() as s:assert record(s.get(Worker,w['id']))['samples']==body['samples']
    engine.dispose()
