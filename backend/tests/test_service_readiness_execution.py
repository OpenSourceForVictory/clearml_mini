"""Real host/container executions; real probe HTTP uploads and cancellation."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from backend.tests.test_service_recovery import service,inspect
from runner.readiness_contract import validate

pytestmark=pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='requires Docker and delegated systemd')
PROGRAM='''
import os,json,hashlib,base64,socket
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.headers.get('Upgrade','').lower()=='websocket':
   key=self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
   self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1(key.encode()).digest()).decode());self.end_headers()
   data=b'{"ready":true}'
   self.wfile.write(bytes([129,len(data)])+data);self.wfile.flush()
   self.connection.settimeout(1)
   try:self.rfile.read(6)
   except OSError:pass
  else:
   self.send_response(200);self.end_headers();self.wfile.write(b'{"ready":true}')
 def log_message(self,*args):pass
ThreadingHTTPServer((os.environ['MINI_EVAL_BIND_HOST'],int(os.environ['MINI_EVAL_HEALTH_PORT'])),Handler).serve_forever()
'''


@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('kind',['tcp','http','handshake','message'])
@pytest.mark.parametrize('independent',[False,True])
def test_real_readiness_gate_and_release(tmp_path,mode,kind,independent):
    build,task=service(tmp_path,PROGRAM,mode)
    c={'kind':'websocket' if kind in ('handshake','message') else kind,'interval':.5,'timeout':1,'stale_after':5,'startup_timeout':15}
    if kind=='message':c.update(mode='message',expect={'type':'json_equals','pointer':'/ready','value':True})
    if independent:
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));health_port=sock.getsockname()[1]
        c['port']=health_port
        task['client_spec']['service']['health_port']=health_port
    task['client_spec']['service']['readiness']=validate(c)
    records=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));records.append(body)
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'accepted':True,'ready':body['ready']}).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    w=build();w.config.update(api_url=f'http://127.0.0.1:{server.server_port}',token='test-only');completed=[];beats=[]
    class API:
        def post(self,path,data=None):
            if path.endswith('/service-probe-session'):return {'epoch':'test-epoch'}
            if path=='worker/heartbeat':
                beats.append(time.monotonic());return {'lease_seconds':10,'cancel':any(x['ready'] for x in records)}
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API()
    try:
        w.execute(task)
        assert any(x['ready'] for x in records),records
        assert records[-1]['epoch']=='test-epoch'
        assert completed[-1]['status']=='CANCELLED'
        assert w.state is None
        assert inspect(w,task) is None
        assert len(beats)>=2
    finally:
        server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('mode',['host','docker'])
def test_probe_timeout_does_not_block_cancel_or_heartbeat(tmp_path,mode):
    import urllib.error
    # WS handshake succeeds, then waits without providing business readiness.
    program=PROGRAM.replace("self.wfile.write(bytes([129,len(data)])+data);self.wfile.flush()", "import time;time.sleep(30)")
    build,task=service(tmp_path,program,mode)
    task['client_spec']['service']['readiness']=validate({'kind':'websocket','mode':'message','path':'/ws','timeout':30,'stale_after':45,'expect':{'type':'text_equals','value':'ready'}})
    w=build();w.config.update(api_url='http://127.0.0.1:1',token='test-only');completed=[];beats=[];begin=time.monotonic()
    class API:
        def post(self,path,data=None):
            if path.endswith('/service-probe-session'):return {'epoch':'test'}
            if path=='worker/heartbeat':
                beats.append(time.monotonic());return {'lease_seconds':10,'cancel':time.monotonic()-begin>2}
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API();w.execute(task)
    assert completed[-1]['status']=='CANCELLED'
    assert time.monotonic()-begin<8
    assert len(beats)>=3
    assert inspect(w,task) is None


@pytest.mark.parametrize('mode',['host','docker'])
def test_startup_deadline_survives_log_upload_failure(tmp_path,mode):
    import urllib.error
    build,task=service(tmp_path,"import time;print('log',flush=True);time.sleep(60)",mode)
    task['client_spec']['service']['readiness']=validate({'kind':'tcp','startup_timeout':5,'interval':.5})
    w=build();w.config.update(api_url='http://127.0.0.1:1',token='test-only');completed=[];begin=time.monotonic()
    class API:
        def post(self,path,data=None):
            if path.endswith('/service-probe-session'):return {'epoch':'test'}
            if path=='worker/heartbeat':return {'lease_seconds':10,'cancel':False}
            if path.endswith('/logs'):raise urllib.error.URLError('test outage')
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API();w.execute(task)
    assert completed[-1]['status']=='FAILED'
    assert 'startup health deadline' in completed[-1]['error']
    assert time.monotonic()-begin<10
    assert inspect(w,task) is None


@pytest.mark.parametrize('mode',['host','docker'])
def test_client_connection_failure_prevents_business_launch(tmp_path,monkeypatch,mode):
    build,task=service(tmp_path,"raise RuntimeError('must never launch')",mode)
    task['benchmark']='cs-client';task['client_spec']['service_mode']='external'
    task['client_spec']['endpoint']='http://192.0.2.1:12345'
    task['client_spec'].pop('service');task['client_spec'].pop('workload',None)
    monkeypatch.setattr('worker.probe_dependencies.ready',lambda:True)
    def fail(endpoint):raise RuntimeError('client_service_unreachable: test isolated client')
    monkeypatch.setattr('worker.client_connectivity.verify',fail)
    w=build();completed=[];paths=[]
    monkeypatch.setattr(w,'create',lambda task:pytest.fail('business launch must not occur'))
    class API:
        def post(self,path,data=None):
            paths.append(path)
            if path=='worker/heartbeat':return {'lease_seconds':10,'cancel':False}
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API();w.execute(task)
    assert completed[-1]['status']=='FAILED'
    assert 'client_service_unreachable' in completed[-1]['error']
    assert not any('service-health' in p for p in paths)
    assert w.state is None
    assert inspect(w,task) is None
