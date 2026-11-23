"""Opt-in local systemd-user/Docker executions with a disposable HTTP fault server.

Real business processes, no GPU/model evaluation or production API state.
"""
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest
from backend.tests.test_host_backend import worker as host_fixture
from worker.client import Worker

pytestmark = pytest.mark.skipif(os.getenv('BACKPRESSURE_REAL_TEST') != '1',
                                reason='requires local systemd user manager and approved Docker test image')


@pytest.mark.parametrize('mode', ['host', 'docker'])
@pytest.mark.parametrize('outage', ['transient', 'lease-expiry'])
def test_real_process_survives_busy_reports_but_stops_at_lease(mode, outage, tmp_path):
    code = "import time; print('started',flush=True); time.sleep(%d); print('done',flush=True)" % (4 if outage == 'transient' else 30)
    fixture = host_fixture(tmp_path, ['python3', '-c', code], timeout=60)
    task = fixture.state['task']
    task.update(status='CLAIMED', lease_seconds=30 if outage == 'transient' else 4, benchmark='cs-client', cpus=0, ram_gb=0)
    template = task['client_spec']['template']
    template.update(task_type='command', integration_mode='direct', execution_mode=mode)
    task['client_spec']['service_mode']='none'
    image = os.getenv('BACKPRESSURE_TEST_IMAGE','mini-eval-runner:dev')
    if mode == 'docker':
        task['image'] = template['image'] = image
        template['runtime'] = {'command_mode':'exec','working_directory':'/tmp','assets':{}}
    events, faults, running_at_fault = [], set(), []
    worker = None
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            kind = self.path.rsplit('/',1)[-1]
            events.append((kind,body))
            status, result = 200, {'accepted':True}
            if kind == 'heartbeat':
                result = {'lease_seconds':task['lease_seconds'],'cancel':False}
            busy = (outage == 'transient' and kind in ('logs','complete') and kind not in faults)
            busy = busy or (outage == 'lease-expiry' and kind == 'heartbeat' and body.get('started') is not False)
            if busy:
                if kind == 'logs':
                    running_at_fault.append(worker.inspect(task['execution_id'])['State']['Running'])
                faults.add(kind);status=503;result={'detail':'injected busy'}
            data=json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Retry-After','0')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config = {**fixture.config, 'api_url':f'http://127.0.0.1:{server.server_port}', 'token':'isolated-test',
              'runner_image':image, 'client_images':[image], '_host_approval':fixture.state['host_approval']}
    worker = Worker(config, tmp_path/'execution-state', snapshot={'revision':0,'config':fixture.config})
    worker.slot_id='isolated-slot'
    execution = task['execution_id']
    begin=time.monotonic()
    try:
        worker.execute(task)
        completions=[body for kind,body in events if kind=='complete']
        assert completions
        if outage == 'transient':
            assert faults == {'logs','complete'}
            assert any(running_at_fault), '503 was injected while business process was alive'
            assert completions[-1]['status']=='SUCCEEDED', completions
            assert completions[-1]['exit_code']==0
            assert completions[0]==completions[1]
        else:
            assert completions[-1]['status']=='FAILED'
            assert 'lease expired' in completions[-1]['error']
            assert time.monotonic()-begin < 20
        assert worker.state is None
        assert not worker.inspect(execution)
    finally:
        try:worker.stop_container(execution);worker.remove(execution)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
