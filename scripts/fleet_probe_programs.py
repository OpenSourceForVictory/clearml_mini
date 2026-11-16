"""Small real HTTP programs; test inference echo, not model quality."""
SERVER = '''import json,os
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{}')
 def do_POST(self):
  v=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'received':v,'model':c['business']['model'],'server_execution':c['platform']['execution_id']}).encode())
ThreadingHTTPServer((c['platform']['listen']['address'],c['platform']['listen']['port']),H).serve_forever()
'''
CLIENT = '''import json,os,time,urllib.request
from pathlib import Path
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
p={'case_id':c['task']['case_id'],'execution_id':c['platform']['execution_id'],'business':c['business']}
u=c['platform']['service']['endpoint']
r=urllib.request.Request(u,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=10) as response:v=json.load(response)
assert v['received']==p and v['model']=='fleet-test-model'
assert c['business']['variant']==int(c['task']['case_id'])
time.sleep(c['business'].get('sleep_seconds',1))
print(json.dumps({'verified_input':p,'endpoint':u,'server_execution':v['server_execution']}),flush=True)
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{'echo_verified':1}}))
'''
COMMAND = '''import json,os,time,ctypes
from pathlib import Path
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
lib=ctypes.CDLL('libcuda.so.1');assert lib.cuInit(0)==0
n=ctypes.c_int();assert lib.cuDeviceGetCount(ctypes.byref(n))==0 and n.value==1
ctx=ctypes.c_void_p();assert lib.cuCtxCreate_v2(ctypes.byref(ctx),0,0)==0
memory=ctypes.c_ulonglong();assert lib.cuMemAlloc_v2(ctypes.byref(memory),ctypes.c_size_t(32*1024*1024))==0
time.sleep(c['business'].get('sleep_seconds',3))
assert lib.cuMemFree_v2(memory)==0
print(json.dumps({'gpu_verified':True,'case_id':c['task']['case_id'],'execution_id':c['platform']['execution_id']}),flush=True)
'''
