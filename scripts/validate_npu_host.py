"""Opt-in local Ascend host acceptance: two services, one card, abnormal exit and cleanup.

Run on a dedicated idle card as the system Worker administrator. No existing
service, driver, account, system configuration or device reset is modified.
"""
import argparse
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--device',type=int,required=True)
parser.add_argument('--user',required=True)
parser.add_argument('--python',required=True)
parser.add_argument('--library-path',default='/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64/common')
args=parser.parse_args()
if args.device<0:parser.error('device must be non-negative')
repository=Path(__file__).resolve().parents[1]
sources={str(p.relative_to(repository)):p.read_text() for package in ('worker','runner','node_agent') for p in (repository/package).glob('*.py')}
import json,sys,tempfile,os,time,uuid,socket,subprocess
from pathlib import Path
from types import SimpleNamespace
with tempfile.TemporaryDirectory(prefix='mini-eval-npu-execution-') as directory:
 root=Path(directory);root.chmod(0o755)
 for name,source in sources.items():
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.parent.chmod(0o755);p.write_text(source);p.chmod(0o644)
 sys.path.insert(0,str(root))
 from worker.host_backend import HostBackend
 from worker.accelerators import ascend_sample
 from worker.workspace import validate_storage
 code=root/'code';code.mkdir();code.chmod(0o755)
 (code/'server.py').write_text("""import ctypes,json,os,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
config=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
a=ctypes.CDLL('libascendcl.so')
assert a.aclInit(None)==0
count=ctypes.c_uint();assert a.aclrtGetDeviceCount(ctypes.byref(count))==0 and count.value==1,count.value
assert a.aclrtSetDevice(0)==0
pointer=ctypes.c_void_p();assert a.aclrtMalloc(ctypes.byref(pointer),ctypes.c_size_t(1024*1024),0)==0
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{}')
Path(os.environ['MINI_EVAL_OUTPUT_DIR']+'/ready.json').write_text(json.dumps({'uid':os.getuid(),'count':count.value,'allocated':1024*1024,'visible':os.environ['ASCEND_RT_VISIBLE_DEVICES']}))
HTTPServer(('127.0.0.1',config['port']),Handler).serve_forever()
""")
 (code/'server.py').chmod(0o644)
 profile={'python':args.python,'code_root':str(code)}
 runtime={'approved':True,'allow_shared':True,'host':{'approved':True,'user':args.user,'environment':{'LD_LIBRARY_PATH':args.library_path}}}
 handles=[];evidence=[]
 try:
  for index in range(2):
   base=root/str(index);base.mkdir();base.chmod(0o755)
   private=base/'private';private.mkdir(mode=0o700);state=private/'state'
   for name in ('assets','cache'):(base/name).mkdir()
   config={'accelerator_type':'npu','gpu_index':args.device,'npu_runtime':runtime,'workspace_root':str(base/'work'),'assets_root':str(base/'assets'),'cache_root':str(base/'cache'),'min_free_disk_gb':0}
   validate_storage(config,state,create=True)
   (base/'work').chmod(0o711);(base/'work/runs').chmod(0o711)
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   task={'execution_id':str(uuid.uuid4()),'job_id':'probe-'+str(index),'episodes':1,'timeout_seconds':90,'storage_revision':0,'host_revision':1,'host_profile':profile,'cpus':1,'ram_gb':2,'accelerator_type':'npu','device_index':args.device,'gpu_uuid':ascend_sample(config)['identity'],'gpu_mode':'shared',
    'client_spec':{'contract_version':1,'task_name':'npu-service-probe','workload':'service','service':{'port':port,'address':'127.0.0.1'},'seed':0,'endpoint':'','template':{'execution_mode':'host','host_runtime':{'environment_name':'probe'},'command':['python3','server.py']}}}
   worker=SimpleNamespace(config=config,path=state,workspace=base/'work',state={'task':task,'host_approval':{'revision':1,'environments':{'probe':profile}}},storage_snapshot={'revision':0},runtime_snapshot={'config':{'assets':{}}})
   backend=HostBackend(worker);handles.append((backend,task));backend.create(task)
   launch=json.loads((backend.root/'launch.json').read_text())
   backend.start(task)
  until=time.monotonic()+25
  while time.monotonic()<until:
   paths=list(root.rglob('ready.json'))
   if len(paths)==2:break
   if any(not backend.inspect(task['execution_id'])['State']['Running'] for backend,task in handles):
    raise RuntimeError(str([(backend.logs(),backend.control('show',backend.unit(task['execution_id']),'--property=Result,ExecMainStatus,User,WorkingDirectory').stdout) for backend,task in handles]))
   time.sleep(.2)
  paths=list(root.rglob('ready.json'))
  assert len(paths)==2,str([backend.logs() for backend,task in handles])
  for p in paths:evidence.append(json.loads(p.read_text()))
  assert all(x['uid']!=0 and x['count']==1 and x['visible']=='0' for x in evidence)
  assert all(backend.inspect(task['execution_id'])['State']['Running'] for backend,task in handles)
  import urllib.request
  for backend,task in handles:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:'+str(task['client_spec']['service']['port']),timeout=5) as response:assert response.status==200
  backend,task=handles[0];backend.control('kill','--signal=KILL',backend.unit(task['execution_id']))
  time.sleep(1)
  assert not backend.inspect(task['execution_id'])['State']['Running']
  assert handles[1][0].inspect(handles[1][1]['execution_id'])['State']['Running']
 finally:
  for backend,task in handles:backend.remove(task['execution_id'])
 print(json.dumps({'services':evidence,'same_device':args.device,'concurrent':True,'abnormal_exit_isolated':True,'cleanup_confirmed':True}))
