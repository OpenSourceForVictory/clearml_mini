import json,os,socket,sys,threading,time,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from worker.client import Worker
from worker.workspace import validate_storage

ROOT=Path('.runtime/bounded-logs-live').resolve()
records={}
class API(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  path=self.path
  execution=body.get('execution_id') if path.endswith('heartbeat') else path.split('/executions/')[-1].split('/')[0]
  record=records.setdefault(execution,{'logs':[],'beats':[]})
  response={'lease_seconds':30,'cancel':False}
  if path.endswith('heartbeat'):record['beats'].append(time.monotonic())
  if path.endswith('logs-v2'):
   record['logs'].append(body);response={'accepted':True,'next_sequence':body['sequence']+1}
  if path.endswith('complete'):record['complete']=body
  if path.endswith('episodes'):record.setdefault('episodes',[]).append(body)
  raw=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',0),API)
threading.Thread(target=server.serve_forever,daemon=True).start()
summary=[]
try:
 for mode in os.getenv('LOG_LIVE_MODES','host,docker').split(','):
  for integration,service in [('command',False),('direct',False),('sdk-command',False),('service',True)]:
   identity=str(uuid.uuid4());root=ROOT/identity;root.mkdir()
   for name in ['assets','cache','workspace']: (root/name).mkdir()
   config={'api_url':f'http://127.0.0.1:{server.server_port}','token':'local-probe-only','workspace_root':str(root/'workspace'),'assets_root':str(root/'assets'),'cache_root':str(root/'cache'),'min_free_disk_gb':0,'client_images':['mini-eval-api:batch-delete-fe7886e-20261118']}
   validate_storage(config,root/'state.json',create=True)
   profile={'python':'/usr/bin/python3','code_root':str(root)}
   config['_host_approval']={'revision':1,'environments':{'live':profile}}
   worker=Worker(config,root/'state.json',snapshot={'revision':0,'config':config})
   worker.slot_id='local-probe'
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   script="import json,sys,time;print('x'*1000000,flush=True);print('TAIL-ERROR-'+"+repr(mode)+",file=sys.stderr,flush=True);time.sleep(1);print(json.dumps({'final':{'success_rate':1,'episode_count':1,'runtime':1}}),flush=True)"
   if service:
    script="import socket,threading,time,sys,json\ns=socket.socket();s.bind(('0.0.0.0',int(__import__('os').environ['MINI_EVAL_PORT'])));s.listen()\nthreading.Thread(target=lambda:s.accept()[0].close(),daemon=True).start()\nprint('x'*1000000,flush=True)\nprint('TAIL-SERVICE-'+"+repr(mode)+",file=sys.stderr,flush=True)\ntime.sleep(3)"
   if integration=='sdk-command':
    script="import sys,time,os;from pathlib import Path;print('x'*1000000,flush=True);print('TAIL-ERROR-"+mode+"',file=sys.stderr,flush=True);Path(os.environ['MINI_EVAL_RESULT']).write_text('{\"success\":true}');time.sleep(1)"
   template={'execution_mode':mode,'task_type':'command' if service or integration=='command' else 'evaluation','integration_mode':'sdk-command' if integration=='sdk-command' else 'direct','command':['/usr/bin/python3' if mode=='host' else 'python','-c',script],'image':config['client_images'][0]}
   if mode=='host':template['host_runtime']={'environment_name':'live','command_mode':'exec','environment':{},'assets':[],'overridable_env':[]}
   else:template['runtime']={'interpreter':'python','environment':{},'assets':{},'overridable_env':[]}
   spec={'contract_version':1,'task_name':'log-live','seed':0,'service_mode':'none','endpoint':'','request_timeout_seconds':2,'template':template}
   if service:spec.update(workload='service',service={'address':'127.0.0.1','port':port,'container_port':port,'protocol':'http','health_type':'tcp','health_path':'/','startup_timeout':15})
   task={'execution_id':identity,'job_id':identity,'episodes':2 if integration=='sdk-command' else 1,'status':'STARTING','lease_seconds':30,'log_protocol':2,'benchmark':'inference-service' if service else 'cs-client','image':template['image'],'gpu_uuid':None,'cpus':0,'ram_gb':0,'timeout_seconds':30,'storage_revision':0,'runtime_revision':0,'host_revision':1,'host_profile':profile,'client_spec':spec}
   if os.getenv('LOG_LIVE_LARGE'):
    template['command'][-1]=template['command'][-1].replace('1000000','4000000')
   worker.execute(task)
   result=records[identity];expected='FAILED' if service else 'SUCCEEDED'
   assert result.get('complete',{}).get('status')==expected,result.get('complete')
   if integration in ('direct','sdk-command'):assert result['complete']['metrics']['episode_count']==task['episodes']
   if integration=='sdk-command':assert len(result.get('episodes',[]))==4
   content=''.join(r['content'] for r in result['logs'])
   marker=('TAIL-SERVICE-' if service else 'TAIL-ERROR-')+mode
   assert marker in content,(mode,service,'missing tail',result.get('complete'))
   item={'mode':mode,'role':'service' if service else 'client','integration':integration,'status':result['complete']['status'],'exit_code':result['complete']['exit_code'],'tail_present':True,'log_requests':len(result['logs']),'heartbeats':len(result['beats']),'max_heartbeat_gap':max([b-a for a,b in zip(result['beats'],result['beats'][1:])] or [0]),'log_gap':result['complete'].get('log_gap',False)}
   summary.append(item);print(json.dumps(item),flush=True)
 (ROOT/'summary.json').write_text(json.dumps({'cases':summary,'boundary':'real local systemd and Docker; loopback control API fixture, CPU-only diagnostic programs, not model evaluation'},indent=2))
finally:server.shutdown()
