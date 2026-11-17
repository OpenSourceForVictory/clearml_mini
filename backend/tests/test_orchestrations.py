import copy
from sqlalchemy import select,func
from backend.tests.test_execution import env,node,claim
from backend.app.core import Slot,Worker,Run,Job,EvaluationSuite,EvaluationBatch,ServiceInstance,now
from backend.app.scheduling import tick


def setup(env,monkeypatch):
    main,c,a=env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','8');monkeypatch.setenv('MAX_ACTIVE_PER_USER','8')
    st,ss=node(env,'server');ct,cs=node(env,'client',count=2)
    with main.SessionLocal() as s:
        for slot in s.scalars(select(Slot)):
            slot.protocol_version=10;slot.client_images=['image:1'];slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
            slot.runtime_report={'revision':0,'applied_revision':0,'images':{'image:1':{'ready':True}},'assets':{}};slot.runtime_reported_at=now()
            w=s.get(Worker,slot.worker_id);w.max_concurrent=4
            if slot.id in ss:w.roles=['server'];w.service_network={'address':'192.168.8.151','port_start':19000,'port_end':19002}
        s.commit()
    t=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={'name':'client','version':'1','image':'image:1','command':['python','client.py']}).json()
    r=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={'name':'server','version':'1','image':'image:1','command':['python','server.py']});assert r.status_code==201,r.text
    server=r.json()
    assert c.patch('/api/v1/admin/server-templates/'+server['id']+'/access',headers=a['admin'],json={'expected_revision':0,'available_to_members':True}).status_code==200
    body={'title':'managed','pool_id':'mock','max_concurrent':2,'client':{'template_id':t['id'],'service_mode':'managed'},
          'cases':[{'case_id':str(i),'task_name':'scene-'+str(i),'episodes':1} for i in range(3)],
          'services':[{'template_id':server['id'],'pool_id':'mock','mode':'shared'}]}
    return st,ss[0],ct,cs,body


def submit(env,body,key='one'):
    r=env[1].post('/api/v1/orchestrations',headers={**env[2]['alice'],'Idempotency-Key':key},json=body)
    assert r.status_code==201,r.text
    return r.json()


def healthy(env,token,slot):
    main,c,_=env;task=claim(env,token,slot);assert task and task['benchmark']=='inference-service'
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'execution_id':task['execution_id']}).status_code==200
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/service-health',headers=token,json={'ready':True}).status_code==200
    return task


def finish(env,token,task,status='SUCCEEDED'):
    body={'status':status}
    if status=='SUCCEEDED':body['metrics']={'success_rate':1,'episode_count':1,'runtime':.1}
    r=env[1].post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json=body)
    assert r.status_code==200,r.text


def test_atomic_idempotent_submission_and_read_only_preflight(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    r=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body);assert r.status_code==200,r.text
    assert r.json()['pair_checks'][0]['pair_ready']
    with main.SessionLocal() as s:
        assert not list(s.scalars(select(Job))) and not list(s.scalars(select(ServiceInstance)))
    suite={'name':'new','version':'1','template_id':body['client']['template_id'],'cases':body['cases']}
    body={**body,'cases':[],'new_suite':suite}
    result=submit(env,body);assert submit(env,body)['id']==result['id']
    assert c.patch('/api/v1/admin/client-templates/'+body['client']['template_id'],headers=a['admin'],json={'enabled':False}).status_code==200
    assert submit(env,body)['id']==result['id']
    r=c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'one'},json={**body,'title':'different'})
    assert r.status_code==409
    assert c.get('/api/v1/orchestrations/'+result['id'],headers=a['bob']).status_code==404
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(EvaluationSuite))==1
        assert s.scalar(select(func.count()).select_from(Job))==3
        assert s.scalar(select(func.count()).select_from(EvaluationBatch))==1


def test_managed_shared_health_gate_binding_and_cleanup(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);batch=submit(env,body)
    assert tick(main.SessionLocal)==2
    assert c.post('/api/v1/worker/slots/'+cs[0]+'/claim',headers=ct).json()['task'] is None
    service=healthy(env,st,ss)
    first=claim(env,ct,cs[0]);second=claim(env,ct,cs[1]);assert first and second
    assert first['client_spec']['endpoint']==second['client_spec']['endpoint']=='http://192.168.8.151:19000'
    detail=c.get('/api/v1/orchestrations/'+batch['id'],headers=a['alice']).json()
    active=[x for x in detail['cases'] if x['status'] in ('STARTING','RUNNING')]
    assert len(active)==2 and all(x['service_execution_id']==service['execution_id'] for x in active)
    assert len(detail['orchestration']['services'])==1
    finish(env,ct,first);third=claim(env,ct,cs[0]);assert third
    finish(env,ct,second);finish(env,ct,third);tick(main.SessionLocal)
    with main.SessionLocal() as s:
        instance=s.scalar(select(ServiceInstance));assert not instance.desired_running and instance.auto_stopped
        assert s.get(Job,instance.current_job_id).status=='CANCEL_REQUESTED'
    finish(env,st,service,'CANCELLED')
    with main.SessionLocal() as s:assert not list(s.scalars(select(Run).where(Run.cleanup_confirmed.is_(False))))


def test_managed_exclusive_single_instance_queues_clients(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    body['services'][0].update(mode='exclusive',max_instances=1)
    submit(env,body);service=healthy(env,st,ss)
    first=claim(env,ct,cs[0]);assert first
    assert claim(env,ct,cs[1]) is None
    finish(env,ct,first)
    second=claim(env,ct,cs[0]);assert second and second['job_id']!=first['job_id']
    assert second['client_spec']['endpoint']==first['client_spec']['endpoint']


def test_invalid_service_rolls_back_and_insufficient_pair_budget_queues(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    bad=copy.deepcopy(body);bad['services'].append({**bad['services'][0],'key':'bad','template_id':'missing'})
    r=c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'bad'},json=bad);assert r.status_code==400
    with main.SessionLocal() as s:assert not list(s.scalars(select(Job)))
    submit(env,body)
    with main.SessionLocal() as s:
        for slot in cs:s.get(Worker,s.get(Slot,slot).worker_id).enabled=False
        s.commit()
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:assert not list(s.scalars(select(ServiceInstance)))


def test_cancel_waiting_dependency_stops_service_without_claiming_client(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);batch=submit(env,body)
    service=healthy(env,st,ss)
    r=c.post('/api/v1/batches/'+batch['id']+'/control',headers=a['alice'],json={'action':'cancel','expected_revision':0})
    assert r.status_code==200,r.text
    tick(main.SessionLocal)
    assert claim(env,ct,cs[0]) is None
    assert c.post('/api/v1/worker/heartbeat',headers=st,json={'slot_id':ss,'execution_id':service['execution_id']}).json()['cancel']


def test_service_network_revision_permissions_and_port_exhaustion(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    with main.SessionLocal() as s:worker=s.get(Slot,ss).worker_id
    path='/api/v1/admin/nodes/'+worker+'/service-network'
    assert c.get(path,headers=a['alice']).status_code==403
    for config in ({'address':'127.0.0.1'},{'address':'192.168.1.1','port_start':20000,'port_end':19000}):
        assert c.put(path,headers=a['admin'],json={'expected_revision':0,'config':config}).status_code in (400,422)
    r=c.put(path,headers=a['admin'],json={'expected_revision':0,'config':{'address':'192.168.8.151','port_start':19000,'port_end':19000}})
    assert r.status_code==200 and r.json()['revision']==1
    assert c.put(path,headers=a['admin'],json={'expected_revision':0,'config':None}).status_code==409
    submit(env,body);tick(main.SessionLocal)
    from backend.app.service_network import choose_port
    with main.SessionLocal() as s:assert choose_port(s,s.get(Worker,worker)) is None


def test_stale_health_blocks_new_claim_and_running_binding_is_immutable(env,monkeypatch):
    from datetime import timedelta
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);batch=submit(env,body)
    service=healthy(env,st,ss);first=claim(env,ct,cs[0]);assert first
    with main.SessionLocal() as s:
        item=s.scalar(select(ServiceInstance));item.health_at=now()-timedelta(seconds=16);s.commit()
    assert claim(env,ct,cs[1]) is None
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==first['execution_id']))
        assert run.assigned_service_execution_id==service['execution_id']
    assert c.get('/api/v1/orchestrations/'+batch['id']+'/services/missing/logs',headers=a['bob']).status_code==404


def test_two_batches_reserve_only_complete_pairs_and_repeated_ticks_are_idempotent(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    first=submit(env,body,'first');second=submit(env,body,'second')
    assert tick(main.SessionLocal)==2
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
        assert len(runs)==2 and sum(r.waiting_service for r in runs)==1
        assert len(list(s.scalars(select(ServiceInstance))))==1
        client=next(r for r in runs if r.waiting_service)
        assert s.get(Job,client.job_id).batch_id in (first['id'],second['id'])


def test_shared_gpu_joint_reservations_do_not_double_count_or_block_server_claim(env,monkeypatch):
    from backend.app.core import Pool
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    with main.SessionLocal() as s:
        p=s.get(Pool,'mock');p.gpu_model='*';p.allow_shared=True;p.max_tasks_per_gpu=4;p.vram_reserve_gb=0
        slot=s.get(Slot,ss);w=s.get(Worker,slot.worker_id);w.roles=['server','client'];w.cpu_capacity=32;w.ram_gb_capacity=64
        slot.gpu_model='GPU';slot.vram_mb=24*1024;slot.free_vram_mb=24*1024;slot.gpu_utilization=0;slot.telemetry_at=now()
        for identity in cs:s.get(Worker,s.get(Slot,identity).worker_id).enabled=False
        s.commit()
    body.update(gpu_mode='shared',requested_vram_gb=8)
    body['services'][0]['resources']={'gpu_mode':'shared','requested_vram_gb':8}
    submit(env,body);assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
        assert sum(r.reserved_vram_mb for r in runs)==16*1024
        assert {r.slot_id for r in runs}=={ss}
    service=healthy(env,st,ss);tick(main.SessionLocal)
    client=c.post('/api/v1/worker/slots/'+ss+'/claim',headers=st,json={'known_execution_ids':[service['execution_id']]}).json()['task']
    assert client and client['benchmark']=='cs-client'
    assert client['client_spec']['endpoint']==service['client_spec']['endpoint']


def test_retained_service_explicit_stop_and_restart_keeps_old_binding(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    body['stop_after_batch']=False;body['cases']=body['cases'][:1]
    batch=submit(env,body);service=healthy(env,st,ss);client=claim(env,ct,cs[0]);finish(env,ct,client);tick(main.SessionLocal)
    detail=c.get('/api/v1/orchestrations/'+batch['id'],headers=a['alice']).json();item=detail['orchestration']['services'][0]
    assert item['desired_running']
    path='/api/v1/orchestrations/'+batch['id']+'/services/'+item['id']+'/control'
    assert c.post(path,headers=a['bob'],json={'action':'stop','expected_revision':item['revision']}).status_code==404
    assert c.post(path,headers=a['alice'],json={'action':'stop','expected_revision':item['revision']}).status_code==200
    assert c.post(path,headers=a['alice'],json={'action':'start','expected_revision':item['revision']+1}).status_code==409
    finish(env,st,service,'CANCELLED')
    assert c.post(path,headers=a['alice'],json={'action':'start','expected_revision':item['revision']+1}).status_code==200
    restarted=healthy(env,st,ss);assert restarted['execution_id']!=service['execution_id']
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.execution_id==client['execution_id']))
        assert r.assigned_service_execution_id==service['execution_id']


def test_port_conflict_retries_bounded_and_health_does_not_survive_generation(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);submit(env,body)
    ports=[]
    for _ in range(3):
        service=claim(env,st,ss);assert service
        ports.append(service['client_spec']['service']['port'])
        r=c.post('/api/v1/worker/executions/'+service['execution_id']+'/complete',headers=st,json={'status':'FAILED','error':'service address is not local or port is already in use'})
        assert r.status_code==200,r.text
    assert ports==[19000,19001,19002]
    assert claim(env,st,ss) is None and claim(env,ct,cs[0]) is None
    with main.SessionLocal() as s:assert len(list(s.scalars(select(ServiceInstance))))==3


def test_external_and_command_orchestrations_and_quota_rejection(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    external=copy.deepcopy(body);external['services']=[];external['client'].update(service_mode='external',endpoints=[{'url':'http://192.168.8.155:9000','mode':'shared'}])
    assert submit(env,external,'external')['orchestration']['service_mode']=='external'
    template=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={'name':'command','version':'1','image':'image:1','task_type':'command','command':['echo','hello']}).json()
    command=copy.deepcopy(external);command['client']={'template_id':template['id'],'service_mode':'none'}
    assert submit(env,command,'command')['total_episodes']==0
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','1')
    r=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body)
    assert r.status_code==400 and 'two concurrent' in r.text


def test_case_service_keys_keep_server_parameters_separate(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    body['services'][0].update(key='red',parameters={'model':'red'})
    body['services'].append({**body['services'][0],'key':'blue','parameters':{'model':'blue'}})
    body['cases']=body['cases'][:2]
    body['cases'][0].update(service_keys=['red'],parameters={'model':'client-red'})
    body['cases'][1].update(service_keys=['blue'],parameters={'model':'client-blue'})
    batch=submit(env,body);service=healthy(env,st,ss)
    client=claim(env,ct,cs[0]);assert client
    chosen=client['client_spec']['service_key'];assert client['client_spec']['parameters']['model']=='client-'+chosen
    assert service['client_spec']['parameters']['model']==chosen
    assert claim(env,ct,cs[1]) is None
    detail=c.get('/api/v1/orchestrations/'+batch['id'],headers=a['alice']).json()
    assert {d['parameters']['model'] for d in detail['orchestration']['definitions']}=={'red','blue'}


def test_stopped_service_restores_on_explicit_retry_and_pause_keeps_service(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);body['cases']=body['cases'][:1]
    batch=submit(env,body);service=healthy(env,st,ss);client=claim(env,ct,cs[0])
    r=c.post('/api/v1/batches/'+batch['id']+'/control',headers=a['alice'],json={'action':'pause','expected_revision':0})
    assert r.status_code==200
    with main.SessionLocal() as s:assert s.scalar(select(ServiceInstance)).desired_running
    finish(env,ct,client,'FAILED');tick(main.SessionLocal);finish(env,st,service,'CANCELLED')
    detail=c.get('/api/v1/batches/'+batch['id'],headers=a['alice']).json();case=detail['cases'][0]
    r=c.post('/api/v1/batches/'+batch['id']+'/retry-failed',headers=a['alice'],json={'items':[{'job_id':case['job_id'],'expected_run_id':case['run_id']}]})
    assert r.status_code==200,r.text
    assert tick(main.SessionLocal)==0
    assert c.post('/api/v1/batches/'+batch['id']+'/control',headers=a['alice'],json={'action':'resume','expected_revision':1}).status_code==200
    newservice=healthy(env,st,ss);newclient=claim(env,ct,cs[0]);assert newclient
    assert newservice['execution_id']!=service['execution_id']
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.job_id==client['job_id']).order_by(Run.attempt)))
        assert [r.assigned_service_execution_id for r in runs]==[service['execution_id'],newservice['execution_id']]


def test_static_pair_capacity_conflict_is_rejected_but_occupancy_is_queueable(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    with main.SessionLocal() as s:
        for slot_id in cs:s.get(Worker,s.get(Slot,slot_id).worker_id).roles=['server']
        s.get(Worker,s.get(Slot,ss).worker_id).roles=['client','server'];s.commit()
    # One client-capable slot can run a client; another server-capable slot
    # lacks network preparation, so capacity is possible and remains queueable.
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body).status_code==200
    body['services'][0]['slot_id']=ss
    r=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body)
    assert r.status_code==400 and '客户端与服务端无法同时分配' in r.text
    assert '客户端池' in r.text and '服务端池' in r.text
    assert '同卡不能同时容纳独占请求' in r.text


def test_unclaimed_server_reservation_expiry_replans_both_sides(env,monkeypatch):
    from datetime import timedelta
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);submit(env,body);tick(main.SessionLocal)
    with main.SessionLocal() as s:
        service=s.scalar(select(ServiceInstance));old_job=service.current_job_id
        run=s.scalar(select(Run).where(Run.job_id==old_job));run.reservation_expires_at=now()-timedelta(seconds=1);s.commit()
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        assert s.get(Job,old_job).status=='CANCELLED'
        held=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
        assert len(held)==2 and sum(r.waiting_service for r in held)==1


def test_forwarded_managed_service_binds_locally_and_advertises_route(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    with main.SessionLocal() as s:
        w=s.get(Worker,s.get(Slot,ss).worker_id)
        w.service_network={**w.service_network,'bind_address':'127.0.0.1'};s.commit()
    submit(env,body)
    service=healthy(env,st,ss)
    assert service['client_spec']['service']['address']=='127.0.0.1'
    assert claim(env,ct,cs[0])['client_spec']['endpoint']=='http://192.168.8.151:19000'


def test_failed_service_batch_cleanup_does_not_block_other_batches(env,monkeypatch):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    first=submit(env,body,'failed-service-cleanup')
    service=claim(env,st,ss);assert service
    finish(env,st,service,'FAILED')
    response=c.post('/api/v1/batches/'+first['id']+'/control',headers=a['alice'],json={'action':'cancel','expected_revision':0})
    assert response.status_code==200,response.text
    second=submit(env,body,'unrelated-next-batch')
    assert tick(main.SessionLocal)==2
    tick(main.SessionLocal)  # cleanup is idempotent on subsequent scheduling ticks
    with main.SessionLocal() as s:
        old=s.scalar(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==first['id']))
        assert not old.desired_running and old.auto_stopped
        assert s.get(Job,old.current_job_id).status=='FAILED'
        assert s.scalar(select(Run).join(Job).where(Job.batch_id==second['id'],Run.status=='RESERVED'))
