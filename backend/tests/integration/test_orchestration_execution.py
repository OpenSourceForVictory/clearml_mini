"""Opt-in real processes/containers, isolated API DB and two logical nodes.

No production registration or inference service is used. Physical multi-host
networking remains a separate deployment acceptance check.
"""
import json,os,socket,subprocess,time,threading
from pathlib import Path
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup,submit
from backend.app.core import Slot,Worker as Node,Job,Run,ServiceInstance,RunLog,now
from backend.app.scheduling import tick
from worker.client import Worker,APIError
from worker.workspace import validate_storage

pytestmark=pytest.mark.skipif(os.getenv('ORCHESTRATION_EXECUTION_TEST')!='1',reason='real process/container execution opt-in')

SERVER='''import json,os
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'model':config['business']['model']}).encode())
 def do_POST(self):
  received=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'model':config['business']['model'],'received':received}).encode())
ThreadingHTTPServer((config['platform']['listen']['address'],config['platform']['listen']['port']),Handler).serve_forever()
'''
CLIENT='''import json,os,time,urllib.request
from pathlib import Path
config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
url=config['platform']['service']['endpoint']
sent={'case_id':config['task']['case_id'],'execution_id':config['platform']['execution_id'],'parameters':config['business']}
request=urllib.request.Request(url,data=json.dumps(sent).encode(),headers={'Content-Type':'application/json'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:data=json.load(response)
assert data['received']==sent
assert data['model']=='test-model',data
assert config['business']['variant']==int(config['task']['case_id'])
time.sleep(0.5)
print(json.dumps({'verified_input':{'case':config['task']['case_id'],'endpoint':url,'business':config['business'],'server':data}}),flush=True)
'''

@pytest.mark.parametrize('mode',['host','docker','host-docker','docker-host'])
@pytest.mark.parametrize('access',['shared','exclusive'])
def test_real_two_logical_nodes_three_cases_multiple_batches(env,monkeypatch,tmp_path,mode,access):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
    address=os.environ.get('ORCHESTRATION_TEST_ADDRESS','192.168.8.151')
    # Select an unused local port without retaining a socket during execution.
    with socket.socket() as sock:sock.bind((address,0));port=sock.getsockname()[1]
    image='mini-eval-orchestration-test:'+tmp_path.name.lower()
    (tmp_path/'server.py').write_text(SERVER);(tmp_path/'client.py').write_text(CLIENT)
    profile={'python':'/usr/bin/python3','code_root':str(tmp_path)}
    if 'docker' in mode:
        (tmp_path/'Dockerfile').write_text('FROM mini-eval-client-base:1\nCOPY server.py client.py /demo/\n')
        subprocess.run(['docker','build','--network=none','-t',image,str(tmp_path)],check=True,capture_output=True)
    client_mode,server_mode=mode.split('-') if '-' in mode else (mode,mode)
    def definition(kind,execution_mode):
        runtime={'host_runtime':{'environment_name':'demo'}} if execution_mode=='host' else {'image':image,'runtime':{}}
        return {**runtime,'execution_mode':execution_mode,'version':access,'name':kind+'-'+mode+'-'+access,'command':['python3',('' if execution_mode=='host' else '/demo/')+kind+'.py']}
    client={**definition('client',client_mode),'task_type':'command'}
    server={**definition('server',server_mode),'startup_timeout':20}
    for kind,definition in [('client',client),('server',server)]:
        response=c.post('/api/v1/admin/'+kind+'-templates',headers=a['admin'],json=definition);assert response.status_code==201,response.text
        if kind=='client':body['client']['template_id']=response.json()['id']
        else:
            body['services'][0].update(template_id=response.json()['id'],mode=access,max_instances=1,parameters={'model':'test-model'})
            assert c.patch('/api/v1/admin/server-templates/'+response.json()['id']+'/access',headers=a['admin'],json={'expected_revision':0,'available_to_members':True}).status_code==200
    for case in body['cases']:case['parameters']={'variant':int(case['case_id'])}
    with main.SessionLocal() as s:
        for slot in s.scalars(select(Slot)):
            w=s.get(Node,slot.worker_id);w.host_environments={'demo':profile};w.host_revision=1
            slot.execution_modes=['docker','host'];slot.client_images=[image]
            slot.host_report={'revision':1,'applied_revision':1,'environments':{'demo':{'ready':True}}};slot.host_reported_at=now()
            slot.runtime_report={'revision':0,'applied_revision':0,'images':{image:{'ready':True}},'assets':{}};slot.runtime_reported_at=now()
            if slot.id==ss:w.service_network={'address':address,'port_start':port,'port_end':port}
        s.commit()
    batches=[submit(env,body,str(i)) for i in range(2)]
    executions={};failures=[];tokens={ss:st,**{slot:ct for slot in cs}}
    class API:
        def __init__(self,token):self.token=token
        def post(self,path,data=None):
            r=c.post('/api/v1/'+path,headers=self.token,json=data or {})
            if not r.is_success:raise APIError(r.status_code,r.text)
            return r.json()
    def launch(task,slot):
        root=tmp_path/task['execution_id'];root.mkdir()
        for name in ('assets','cache'): (root/name).mkdir()
        config={'workspace_root':str(root/'work'),'assets_root':str(root/'assets'),'cache_root':str(root/'cache'),'min_free_disk_gb':0,'_host_approval':{'revision':1,'environments':{'demo':profile}},'mock':True}
        validate_storage(config,root/'state',create=True)
        worker=Worker(config,root/'state',snapshot={'revision':0,'config':config},runtime_snapshot={'revision':0,'config':{'images':[image],'assets':{}}})
        worker.slot_id=slot;worker.api=API(tokens[slot])
        def run():
            try:worker.execute(task)
            except BaseException as exc:failures.append(str(exc))
        thread=threading.Thread(target=run);executions[task['execution_id']]=(worker,thread,task);thread.start()
    try:
        deadline=time.monotonic()+100
        while time.monotonic()<deadline:
            with main.SessionLocal() as s:
                # Discovery is a test fixture; process execution and reports are real.
                for slot in s.scalars(select(Slot)):
                    slot.last_seen_at=now();slot.runtime_reported_at=now();slot.storage_reported_at=now();slot.host_reported_at=now()
                s.commit()
            tick(main.SessionLocal)
            for slot,token in tokens.items():
                r=c.post('/api/v1/worker/slots/'+slot+'/claim',headers=token,json={'known_execution_ids':list(executions)});assert r.status_code==200,r.text
                if r.json()['task']:launch(r.json()['task'],slot)
            with main.SessionLocal() as s:
                jobs=list(s.scalars(select(Job).where(Job.batch_id.in_([b['id'] for b in batches]))))
                if any(j.status in ('FAILED','TIMED_OUT') for j in jobs):pytest.fail(str([(j.status,j.error) for j in jobs]))
                services=list(s.scalars(select(ServiceInstance)))
                failed_services=[s.get(Job,i.current_job_id) for i in services if s.get(Job,i.current_job_id).status in ('FAILED','TIMED_OUT')]
                assert not failed_services,[(j.status,j.error) for j in failed_services]
                if all(j.status=='SUCCEEDED' for j in jobs) and services and all(not i.desired_running for i in services) and all(not t.is_alive() for _,t,_ in executions.values()):break
            if failures:pytest.fail(str(failures))
            time.sleep(.2)
        else:pytest.fail('real execution did not drain within deadline')
        with main.SessionLocal() as s:
            assert len(jobs)==6 and len(services)==2
            assert not list(s.scalars(select(Run).where(Run.cleanup_confirmed.is_(False))))
            output=''.join(s.scalars(select(RunLog.content)))
            assert output.count('verified_input')==6
            assert len(executions)==8
            evidence=os.environ.get('ORCHESTRATION_EVIDENCE_DIR')
            if evidence:
                directory=Path(evidence);directory.mkdir(parents=True,exist_ok=True)
                (directory/(mode+'-'+access+'.json')).write_text(json.dumps({'mode':mode,'access':access,'batches':[b['id'] for b in batches],'clients':len(jobs),'services':len(services),'executions':len(executions),'all_succeeded':all(j.status=='SUCCEEDED' for j in jobs),'cleanup_pending':0,'echoes':[json.loads(line)['verified_input'] for line in output.splitlines() if line.startswith('{"verified_input"')]},indent=2))
    finally:
        for worker,_,_ in executions.values():worker.stop_event.set()
        for worker,thread,task in executions.values():
            thread.join(timeout=20)
            try:worker.remove(task['execution_id'])
            except Exception:pass
        if 'docker' in mode:subprocess.run(['docker','image','rm',image],capture_output=True)
