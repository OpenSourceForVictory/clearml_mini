import ctypes,json,os,sys,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
card=os.environ['ASCEND_RT_VISIBLE_DEVICES'];execution=json.load(open(os.environ['MINI_EVAL_INPUT']))['execution_id']
acl=ctypes.CDLL('libascendcl.so')
assert acl.aclInit(None)==0
assert acl.aclrtSetDevice(0)==0
ptr=ctypes.c_void_p();assert acl.aclrtMalloc(ctypes.byref(ptr),ctypes.c_size_t(1024*1024),2)==0
assert acl.aclrtFree(ptr)==0
print('NPU_ALLOC_FREE_OK card='+card+' execution='+execution,flush=True)
print('NPU_STDERR card='+card+' 日志独立',file=sys.stderr,flush=True)
# Delayed readiness proves that launching is not already healthy.
time.sleep(8)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'card':card,'execution':execution,'ready':True}).encode())
 def do_HEAD(self):self.send_response(200);self.end_headers()
 def log_message(self,*args):print('HTTP_REQUEST card='+card+' path='+self.path,flush=True)
print('SERVICE_LISTENING card='+card,flush=True)
ThreadingHTTPServer((os.environ['MINI_EVAL_BIND_HOST'],int(os.environ['MINI_EVAL_PORT'])),Handler).serve_forever()
