from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Slot,Worker,Run,Job,now
from backend.app.scheduling import tick
from sqlalchemy import select


def setup(env):
    main,c,a=env;token,slots=node(env);n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'roles':['client','server']})
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=7;slot.client_images=['image:1']
        slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
        slot.runtime_report={'revision':0,'applied_revision':0,'images':{'image:1':{'ready':True}},'assets':{}};slot.runtime_reported_at=now();s.commit()
    t=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={'name':'server','version':'1','image':'image:1','command':['python3','server.py']}).json()
    return token,slots[0],{'name':'instance','template_id':t['id'],'slot_id':slots[0],'address':'192.168.8.151','port':19093}


def test_service_competes_with_evaluation_and_port_protected(env):
    main,c,a=env;token,slot,body=setup(env)
    job=submit(env);task=claim(env,token,slot);assert task
    r=c.post('/api/v1/admin/services',headers=a['admin'],json=body);assert r.status_code==201,r.text
    instance=r.json();tick(main.SessionLocal)
    with main.SessionLocal() as s:assert s.get(Job,instance['current_job_id']).status=='PENDING'
    assert c.post('/api/v1/admin/services',headers=a['admin'],json={**body,'name':'duplicate'}).status_code==409
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'test'}).status_code==200
    service=claim(env,token,slot);assert service and service['benchmark']=='inference-service'
    assert service['client_spec']['workload']=='service'
    assert c.post('/api/v1/worker/executions/'+service['execution_id']+'/service-health',headers=token,json={'ready':True}).status_code==200
    identity=instance['id'];r=c.post('/api/v1/admin/services/'+identity+'/control',headers=a['admin'],json={'action':'stop','expected_revision':0});assert r.status_code==200
    assert c.post('/api/v1/admin/services/'+identity+'/control',headers=a['admin'],json={'action':'start','expected_revision':1}).status_code==409
    assert c.post('/api/v1/worker/executions/'+service['execution_id']+'/complete',headers=token,json={'status':'CANCELLED'}).status_code==200
    r=c.post('/api/v1/admin/services/'+identity+'/control',headers=a['admin'],json={'action':'start','expected_revision':1});assert r.status_code==200,r.text
    assert r.json()['current_job_id']!=instance['current_job_id']


def test_server_permissions_and_role(env):
    _,c,a=env;token,slot,body=setup(env)
    assert c.post('/api/v1/admin/services',headers=a['alice'],json=body).status_code==403
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'roles':['client']})
    assert c.post('/api/v1/admin/services',headers=a['admin'],json=body).status_code==409


def test_typed_service_validation_claim_and_restart_snapshot(env):
    from backend.tests.test_typed_template_flow import SCHEMA
    from backend.app.core import ServiceInstance,ServerTemplate
    main,c,a=env;token,slot,body=setup(env)
    response=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={
        'name':'typed-server','version':'1','image':'image:1','command':['python3','server.py'],'parameter_schema':SCHEMA})
    assert response.status_code==201,response.text
    template=response.json();body['template_id']=template['id']
    clone=c.post('/api/v1/admin/server-templates/'+template['id']+'/clone',headers=a['admin'],json={'version':'2'})
    assert clone.status_code==201 and clone.json()['parameter_schema']==SCHEMA
    for values in ({},{'scene':42}):
        response=c.post('/api/v1/admin/services',headers=a['admin'],json={**body,'parameters':values})
        assert response.status_code==400,response.text
    with main.SessionLocal() as s:
        assert not list(s.scalars(select(ServiceInstance)))
        assert not list(s.scalars(select(Job)))
    body.update(parameters={'scene':'room with spaces'},parameter_overrides={'difficulty':4})
    response=c.post('/api/v1/admin/services',headers=a['admin'],json=body)
    assert response.status_code==201,response.text
    instance=response.json();expected={'scene':'room with spaces','difficulty':4}
    assert instance['parameters']==expected
    assert tick(main.SessionLocal)==0  # Worker 7 cannot bind typed values.
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=8;s.commit()
    task=claim(env,token,slot);assert task
    assert task['client_spec']['parameters']==expected
    assert task['client_spec']['template']['parameter_schema']==SCHEMA
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'test exit'}).status_code==200
    with main.SessionLocal() as s:
        t=s.get(ServerTemplate,template['id']);t.definition={**t.definition,'parameter_schema':{'version':1,'fields':{}}};s.commit()
    response=c.post('/api/v1/admin/services/'+instance['id']+'/control',headers=a['admin'],json={'action':'start','expected_revision':0})
    assert response.status_code==200,response.text
    task=claim(env,token,slot)
    assert task['client_spec']['parameters']==expected
    assert task['client_spec']['template']['parameter_schema']==SCHEMA


def test_service_health_rejects_evaluation_execution(env):
    _,c,_=env;token,slots=node(env);submit(env);task=claim(env,token,slots[0])
    response=c.post('/api/v1/worker/executions/'+task['execution_id']+'/service-health',headers=token,json={'ready':True})
    assert response.status_code==409


def test_real_host_service_receives_business_values(tmp_path,monkeypatch):
    import json,sys,pytest
    from runner import service_entry
    from backend.tests.test_typed_template_flow import SCHEMA
    script=tmp_path/'business.py'
    script.write_text("import os,sys\nassert sys.argv[1:]==['--scene','room; untouched']\nassert os.environ['DIFFICULTY']=='4'\nassert os.environ['MINI_EVAL_PORT']=='19093'\n")
    command=[sys.executable,str(script)]
    config={'port':19093,'bind_host':'127.0.0.1','template':{'execution_mode':'host','command':command,'parameter_schema':SCHEMA},
            '_resolved_command':command,'_resolved_environment':{'MINI_EVAL_WORK_DIR':str(tmp_path)},'parameters':{'scene':'room; untouched','difficulty':4}}
    path=tmp_path/'input.json';path.write_text(json.dumps(config));monkeypatch.setenv('MINI_EVAL_INPUT',str(path))
    with pytest.raises(SystemExit) as result:service_entry.main()
    assert result.value.code==0


def test_health_freshness_and_offline_are_not_healthy(env):
    from datetime import timedelta
    from backend.app.core import ServiceInstance
    main,c,a=env;token,slot,body=setup(env)
    instance=c.post('/api/v1/admin/services',headers=a['admin'],json=body).json()
    task=claim(env,token,slot)
    path='/api/v1/worker/executions/'+task['execution_id']+'/service-health'
    assert c.post(path,headers=token,json={'ready':True}).status_code==200
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'execution_id':task['execution_id']}).status_code==200
    def state():return c.get('/api/v1/admin/services',headers=a['admin']).json()[0]['state']
    assert state()=='HEALTHY'
    with main.SessionLocal() as s:
        s.get(ServiceInstance,instance['id']).health_at=now()-timedelta(seconds=30);s.commit()
    assert state()=='UNKNOWN'
    assert c.post(path,headers=token,json={'ready':True}).status_code==200
    with main.SessionLocal() as s:
        s.get(Slot,slot).last_seen_at=now()-timedelta(hours=1);s.commit()
    assert state()=='UNKNOWN'


def test_local_probe_rejects_redirect_and_occupied_port():
    import threading,pytest
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from worker.services import health,check_bind
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path=='/healthz' else 302)
            self.send_header('Location','/healthz');self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    service={'address':'127.0.0.1','port':server.server_port,'protocol':'http','health_type':'http','health_path':'/healthz'}
    try:
        assert health(service)['ready']
        assert not health({**service,'health_path':'/redirect'})['ready']
        assert health({**service,'health_type':'tcp'})['ready']
        with pytest.raises(RuntimeError,match='already in use'):check_bind(service)
    finally:
        server.shutdown();thread.join();server.server_close()
    assert not health(service)['ready']


def test_expired_service_keeps_gpu_and_port_until_cleanup(env):
    from datetime import timedelta
    main,c,a=env;token,slot,body=setup(env)
    instance=c.post('/api/v1/admin/services',headers=a['admin'],json=body).json()
    task=claim(env,token,slot);execution=task['execution_id']
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'execution_id':execution}).status_code==200
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==execution))
        run.lease_expires_at=now()-timedelta(minutes=5);s.commit()
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==execution))
        assert run.status=='TIMED_OUT' and not run.cleanup_confirmed
    assert c.post('/api/v1/admin/services',headers=a['admin'],json={**body,'name':'conflict'}).status_code==409
    assert c.post('/api/v1/admin/services/'+instance['id']+'/control',headers=a['admin'],json={'action':'start','expected_revision':0}).status_code==409
    submit(env);assert claim(env,token,slot) is None
    assert c.post('/api/v1/worker/executions/'+execution+'/service-health',headers=token,json={'ready':True}).status_code==409
    assert c.post('/api/v1/worker/executions/'+execution+'/cleanup',headers=token).status_code==200
    assert claim(env,token,slot) is not None
    assert c.post('/api/v1/admin/services',headers=a['admin'],json={**body,'name':'after cleanup'}).status_code==201
