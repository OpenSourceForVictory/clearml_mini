"""Real Agent process + real HTTP; fake systemctl confines control to this test."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
import uvicorn
from backend.tests.test_execution import env,node
from backend.tests.test_node_agents import beat


def wait(check,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=check()
        if value:return value
        time.sleep(.1)
    raise AssertionError('integration state did not converge')


def test_real_agent_http_drain_restart_and_event_replay(env,tmp_path):
    main,c,a=env;token,_=node(env,count=2)
    identity=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]['id']
    path='/api/v1/admin/nodes/'+identity+'/agent'
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(main.app,log_level='critical',lifespan='off'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    wait(lambda:server.started)
    bin=tmp_path/'bin';bin.mkdir();state=tmp_path/'units.json';state.write_text('{}')
    tool=bin/'systemctl'
    tool.write_text('#!'+sys.executable+'\n'+'''import sys,json,os
from pathlib import Path
p=Path(os.environ['TEST_UNITS']);s=json.loads(p.read_text());action,name=sys.argv[-2:]
if action=='start':s[name]='active'
elif action=='stop':s[name]='inactive'
p.write_text(json.dumps(s));print(s.get(name,'inactive'))
''');tool.chmod(0o755)
    config=tmp_path/'config.json';config.write_text(json.dumps({'api_url':f'http://127.0.0.1:{port}','token':token['Authorization'].split()[1],'state_dir':str(tmp_path/'control'),'services':[{'scope':'user','name':f'mini-eval-worker@gpu{i}.service'} for i in range(2)]}));config.chmod(0o600)
    child_env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'TEST_UNITS':str(state)}
    process=None
    def start():return subprocess.Popen([sys.executable,'-m','node_agent.client','--config',str(config)],env=child_env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def status():return c.get(path,headers=a['admin']).json()
    try:
        process=start();wait(lambda:status().get('config_status')=='applied')
        assert len(status()['programs'])==2 and all(x['state']=='active' for x in status()['programs'])
        r=c.put(path,headers=a['admin'],json={'expected_revision':0,'programs':'stopped','heartbeat_seconds':2,'sample_seconds':5});assert r.status_code==200
        wait(lambda:status().get('applied_revision')==1 and status().get('config_status')=='applied',20)
        assert set(json.loads(state.read_text()).values())=={'inactive'}
        events=status()['events'];assert len({e['sequence'] for e in events})==len(events)
        process.terminate();process.wait(15)
        process=start();wait(lambda:status().get('config_status')=='applied' and status().get('sequence',0)>=2)
        assert status()['desired']['programs']=='stopped'
        assert set(json.loads(state.read_text()).values())=={'inactive'}
        r=c.put(path,headers=a['admin'],json={'expected_revision':1,'programs':'running','heartbeat_seconds':2,'sample_seconds':5});assert r.status_code==200
        wait(lambda:status().get('applied_revision')==2 and status().get('config_status')=='applied')
        assert set(json.loads(state.read_text()).values())=={'active'}
    finally:
        if process and process.poll() is None:process.terminate();process.wait(15)
        server.should_exit=True;thread.join(5);sock.close()
