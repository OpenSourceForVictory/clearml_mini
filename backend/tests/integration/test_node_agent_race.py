"""Desired configuration and latest observations survive concurrent reports."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.core import Base,User,Worker,Run,Slot,session_factory
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


def test_multicard_sampling_and_config_remain_consistent_under_concurrent_heartbeats(env, monkeypatch):
    import time
    from sqlalchemy import event
    main, _, _ = env
    factory = session_factory(os.environ['TEST_DATABASE_URL'])
    monkeypatch.setattr(main, 'SessionLocal', factory)
    suffix = uuid.uuid4().hex
    with factory() as s:
        user = User(username='multicard-'+suffix, role='admin', password_hash='unused')
        s.add(user); s.commit(); uid = user.id
    auth = {'Authorization': 'Bearer '+jwt.encode({'sub':uid}, main.SECRET, algorithm='HS256')}
    c = TestClient(main.app)
    node = c.post('/api/v1/admin/worker-credentials', headers=auth,
                  json={'node_name':'multicard-'+suffix, 'max_concurrent':3}).json()
    token = {'Authorization':'Bearer '+node['token']}
    slots = []
    for index in range(3):
        result = c.post('/api/v1/worker/register', headers=token,
            json={'gpu_index':index, 'gpu_uuid':f'mock:{suffix}:{index}',
                  'gpu_model':'virtual', 'vram_gb':1})
        assert result.status_code == 200, result.text
        slots.append(result.json()['slot_id'])
    registration = c.post('/api/v1/agent/register', headers=token,
                          json={'boot_id':suffix, 'version':'1', 'resource_protocol':1})
    assert registration.status_code == 200, registration.text
    sample_at = time.time()
    values = {}
    for index in range(3):
        prefix = f'gpu_{index}_'
        values.update({prefix+'uuid':f'mock:{suffix}:{index}', prefix+'total_mb':1024,
                       prefix+'free_mb':768-index, prefix+'utilization':10+index})
    report = {'session':registration.json()['session'], 'sequence':1,
              'applied_revision':0, 'config_status':'waiting',
              'samples':{'gpu':{'status':'ok', 'sampled_at':sample_at, 'values':values}}}
    barrier = Barrier(5)
    def config():
        barrier.wait()
        return c.put('/api/v1/admin/nodes/'+node['id']+'/agent', headers=auth,
                     json={'expected_revision':0, 'heartbeat_seconds':10})
    def observe():
        barrier.wait()
        return c.post('/api/v1/agent/heartbeat', headers=token, json=report)
    def beat(slot_id):
        barrier.wait()
        return c.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id':slot_id})
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(config), executor.submit(observe)]
        futures.extend(executor.submit(beat, slot) for slot in slots)
        results = [future.result(timeout=10) for future in futures]
    assert [result.status_code for result in results] == [200]*5
    with factory() as s:
        worker = s.get(Worker, node['id'])
        state = record(worker)
        assert state['revision'] == 1 and state['desired']['heartbeat_seconds'] == 10
        assert state['sequence'] == 1 and state['samples'] == report['samples']
        for index, slot_id in enumerate(slots):
            slot = s.get(Slot, slot_id)
            assert slot.free_vram_mb == 768-index
            assert slot.gpu_utilization == 10+index
            assert abs(slot.telemetry_at.timestamp()-sample_at) < .01
    writes = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith('update gpu_slots'):
            writes.append(statement)
    engine = factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        repeated = c.post('/api/v1/agent/heartbeat', headers=token,
                          json={**report, 'sequence':2, 'applied_revision':1})
        assert repeated.status_code == 200, repeated.text
        assert writes == [], 'unchanged sample must not lock all Slot rows again'
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
    assert c.post('/api/v1/agent/heartbeat', headers=token,
                  json={**report, 'sequence':1, 'samples':{}}).status_code == 200
    with factory() as s:
        assert record(s.get(Worker, node['id']))['samples'] == report['samples']
    # An admin configuration writer may hold Worker while ordinary per-card
    # liveness still needs to commit. These reports carry no config ACK.
    with factory() as locker:
        locker.scalar(select(Worker).where(Worker.id == node['id']).with_for_update())
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                pending = [executor.submit(c.post, '/api/v1/worker/heartbeat',
                    headers=token, json={'slot_id':slot_id}) for slot_id in slots]
                assert [future.result(timeout=2).status_code for future in pending] == [200]*3
        finally:
            locker.rollback()
    factory.kw['bind'].dispose()
