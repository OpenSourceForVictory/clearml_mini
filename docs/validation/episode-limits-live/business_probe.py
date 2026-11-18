"""Bounded hardware/launch acceptance; not a model benchmark."""
import argparse,json,os,pwd,sys,time,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--device',choices=['gpu','npu','cpu'],required=True);p.add_argument('--format',default='command');p.add_argument('--fail',default='none');p.add_argument('--scene',default='');p.add_argument('--hold',type=float,default=0);p.add_argument('--server',action='store_true');p.add_argument('--ready-delay',type=float,default=5)
a=p.parse_args();episode=int(os.getenv('MINI_EVAL_EPISODE_ID','1'))
assert os.environ.get('VALIDATION_FLAG')=='template-value'
assert a.scene=='参数 with spaces'
identity=pwd.getpwuid(os.getuid())
with tempfile.TemporaryDirectory(prefix='mini-eval-live-',dir='/tmp') as outside:
    Path(outside,'write-test.txt').write_text('outside-workspace-ok')
    assert Path(outside,'write-test.txt').read_text()=='outside-workspace-ok'
print(json.dumps({'probe':'outside-workspace-write-ok','user':identity.pw_name,'path':'/tmp'}),flush=True)
assert os.environ.get('HOME')==identity.pw_dir
if not a.server and a.format in ('direct','sdk'):
    import urllib.request
    endpoint=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())['endpoint']
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(endpoint,timeout=5) as response:
        assert response.status==200 and json.load(response)['ready'] is True
    print(json.dumps({'probe':'service-ready-before-client','time':time.time()}),flush=True)
if a.device!='cpu':
    import torch
    if a.device=='npu':import torch_npu
    module=torch.npu if a.device=='npu' else torch.cuda
    assert module.device_count()==1,module.device_count()
    x=torch.tensor([2.,3.],device=a.device.replace('gpu','cuda')+':0')
    assert (x*x).cpu().tolist()==[4.,9.]
print(json.dumps({'probe':'hardware-and-input-ok','episode':episode,'user':identity.pw_name,'device':a.device,'visible':os.getenv('CUDA_VISIBLE_DEVICES') if a.device=='gpu' else os.getenv('ASCEND_RT_VISIBLE_DEVICES'),'scene':a.scene,'time':time.time()}),flush=True)
if a.server:
    from http.server import BaseHTTPRequestHandler,HTTPServer
    start=time.monotonic()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ready=time.monotonic()-start>=a.ready_delay
            self.send_response(200 if ready else 503);self.end_headers();self.wfile.write(json.dumps({'ready':ready}).encode())
    HTTPServer(('0.0.0.0',int(os.environ['MINI_EVAL_PORT'])),Handler).serve_forever()
if a.hold:time.sleep(a.hold)
if a.fail=='all' or (a.fail=='first' and episode==1):raise SystemExit(1)
if a.format=='direct':print(json.dumps({'final':{'episode_count':1,'success_rate':1,'runtime':0.01}}),flush=True)
if a.format=='sdk':Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{'hardware_ok':1}}))
