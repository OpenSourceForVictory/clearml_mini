"""Real Docker/host service processes with failures injected at the API boundary."""
import json
import os
import socket
import urllib.error

import pytest
from backend.tests.test_host_backend import worker
from worker.client import Worker
from worker.host_backend import HostBackend

pytestmark=pytest.mark.skipif(os.getenv('HOST_EXECUTION_TEST')!='1',reason='requires local Docker image and real systemd opt-in')


def service(tmp_path,program,mode):
    (tmp_path/'business.py').write_text(program)
    fake=worker(tmp_path,['python3','business.py'])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    task={**fake.state['task'],'benchmark':'inference-service','status':'STARTING','lease_seconds':10}
    spec=task['client_spec'];spec['workload']='service';spec['template']['integration_mode']='direct'
    spec['service']={'address':'127.0.0.1','port':port,'container_port':port,'health_type':'http','protocol':'http','health_path':'/healthz','startup_timeout':5}
    if mode=='docker':
        task['image']='mini-eval-runner:dev'
        spec['template']={'execution_mode':'docker','image':task['image'],'runtime':{},'command':['python3','-c',program]}
    cfg={**fake.config,'_host_approval':fake.state['host_approval']}
    state=tmp_path/'service-state'
    snapshot={'revision':0,'config':fake.config};runtime={'revision':0,'config':{'images':['mini-eval-runner:dev'],'assets':{}}}
    def build():
        w=Worker(cfg,state,snapshot=snapshot,runtime_snapshot=runtime);w.slot_id='test';return w
    return build,task


@pytest.fixture(params=['host','docker'])
def mode(request):return request.param


def inspect(w,task):
    return HostBackend(w).inspect(task['execution_id']) if task['client_spec']['template']['execution_mode']=='host' else w.inspect(task['execution_id'])


def remove(w,task):
    if task['client_spec']['template']['execution_mode']=='host':HostBackend(w).remove(task['execution_id'])
    else:w.remove(task['execution_id'])


@pytest.mark.parametrize('program',[
    'raise SystemExit(23)',
    'print(\'{"final":{"success_rate":1,"episode_count":1,"runtime":0}}\',flush=True)',
])
def test_service_exit_is_failure_even_with_final_output(tmp_path,program,mode):
    build,task=service(tmp_path,program,mode);w=build();completed=[]
    class API:
        def post(self,path,data=None):
            if path=='worker/heartbeat':return {'lease_seconds':10,'cancel':False}
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API()
    try:w.execute(task)
    finally:remove(w,task)
    assert len(completed)==1 and completed[0]['status']=='FAILED'
    assert completed[0].get('metrics') is None
    assert 'runner exited with code' in completed[0]['error'],completed
    assert w.state is None


@pytest.mark.parametrize('lost_reports',[False,True])
def test_service_startup_health_deadline_stops_real_process(tmp_path,mode,lost_reports):
    build,task=service(tmp_path,'import time;time.sleep(60)',mode);w=build();completed=[]
    class API:
        def post(self,path,data=None):
            if path=='worker/heartbeat':return {'lease_seconds':10,'cancel':False}
            if lost_reports and path.endswith('/service-health'):raise urllib.error.URLError('report unavailable')
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API()
    try:
        w.execute(task)
        assert completed[0]['status']=='FAILED'
        assert 'startup health deadline' in completed[0]['error']
        item=inspect(w,task)
        assert not item or not item['State']['Running']
        assert w.state is None
    finally:remove(w,task)


def test_transient_health_upload_keeps_service_until_cancel(tmp_path,monkeypatch,mode):
    build,task=service(tmp_path,'import time;time.sleep(60)',mode);w=build();completed=[]
    monkeypatch.setattr('worker.client.service_health',lambda _: {'ready':True,'error':None})
    class API:
        reports=0
        def post(self,path,data=None):
            if path=='worker/heartbeat':return {'lease_seconds':10,'cancel':self.reports>=2}
            if path.endswith('/service-health'):
                self.reports+=1
                if self.reports==1:raise urllib.error.URLError('one report lost')
                assert inspect(w,task)['State']['Running']
            if path.endswith('/complete'):completed.append(data)
            return {}
    w.api=API()
    try:
        w.execute(task)
        assert w.api.reports==2 and completed[0]['status']=='CANCELLED',completed
        assert w.state is None
    finally:remove(w,task)


def test_lost_lease_preserves_journal_until_reconnect_cleanup(tmp_path,mode):
    build,task=service(tmp_path,'import time;time.sleep(60)',mode);task['lease_seconds']=3;w=build()
    class Offline:
        calls=0
        def post(self,path,data=None):
            self.calls+=1
            if self.calls==1:return {'lease_seconds':3,'cancel':False}
            raise urllib.error.URLError('disconnected')
    w.api=Offline()
    try:
        with pytest.raises(urllib.error.URLError):w.execute(task)
        assert w.state and w.state['created']
        assert json.loads(w.path.read_text())['task']['execution_id']==task['execution_id']
        item=inspect(w,task)
        assert not item or not item['State']['Running']
        recovered=build();ack=[]
        class Reconnected:
            def post(self,path,data=None):ack.append(path);return {}
        recovered.api=Reconnected();recovered.cleanup(task['execution_id'])
        assert ack==['worker/executions/'+task['execution_id']+'/cleanup']
        assert recovered.state is None and json.loads(recovered.path.read_text()) is None
    finally:remove(w,task)
