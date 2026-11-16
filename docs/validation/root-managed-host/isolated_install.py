"""Destructive fixture for a disposable systemd container only, never a fleet node.
Unpack the installation bundle in /tmp/bundle; provide /usr/bin/python3 and D-Bus.
Run MINI_EVAL_DISPOSABLE_INSTALL=1 python3 isolated_install.py.
"""
import json,sys,threading,subprocess,pwd,time,os
if os.getenv('MINI_EVAL_DISPOSABLE_INSTALL')!='1':raise SystemExit('requires explicit disposable-container opt-in')
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  data={'node_name':'isolated-install','capabilities':['poolless_registration']}
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_POST(self):
  self.send_response(503);self.end_headers();self.wfile.write(b'{}')
server=ThreadingHTTPServer(('127.0.0.1',18099),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
sys.path.insert(0,'/tmp/bundle')
from scripts.install_system_node import install
credential={'node_name':'isolated-install','api_url':'http://127.0.0.1:18099','token':'ISOLATED-TEST-ONLY','execution_modes':['host'],'runner_image':'unused','mock':True}
layout=install(credential,'0,1')
account=pwd.getpwnam('mini-eval');assert account.pw_uid==account.pw_gid==29000
units=[x['name'] for x in layout['services']]+['mini-eval-agent.service','mini-eval-updater.service']
for unit in units:
 subprocess.run(['systemctl','is-active','--quiet',unit],check=True)
for config in layout['config_paths']:
 p=Path(config);assert p.stat().st_mode&0o777==0o600
 assert json.loads(p.read_text())['host_execution']['user']=='mini-eval'
from worker.host_identity import verify_system
verify_system(account)
try:install(credential,'0,1')
except ValueError:pass
else:raise AssertionError('duplicate installation accepted')
for unit in units:subprocess.run(['systemctl','stop',unit],check=True)
result={'systemd':'running','business_uid':account.pw_uid,'worker_units':2,'agent_and_updater_started':True,'private_configs':True,'business_system_limits_verified':True,'duplicate_install_rejected':True,'cleanup':'all test units stopped','api':'stub, returns 503 for work; no production registration','devices':'mock indices 0,1; no hardware inference'}
Path('/tmp/result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
