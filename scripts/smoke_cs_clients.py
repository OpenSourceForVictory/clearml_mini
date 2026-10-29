"""Disposable API/PostgreSQL/Docker acceptance; never point at production."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from scripts.cs_test_service import Handler
from scripts.smoke_shared_gpu import request, wait, BASE


def main():
    image = os.getenv('CS_TEST_IMAGE', 'mini-eval-cs-test:1')
    host = os.environ['CS_TEST_HOST']
    token = request('auth/login', {'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']})['access_token']
    suffix = uuid.uuid4().hex[:8]
    pool = request('admin/pools', {'name': 'cs-test-'+suffix, 'gpu_model': 'virtual'}, token)['id']
    credential = request('admin/worker-credentials', {'node_name': 'cs-test-'+suffix, 'max_concurrent': 1}, token)
    template = request('admin/client-templates', {'name': 'SDK acceptance '+suffix, 'version': '1', 'image': image,
        'command': ['python', '-m', 'runner.sdk', 'runner.sdk_demo:DemoClient'], 'episode_reporting': True}, token)['id']
    server = ThreadingHTTPServer((host, 19090), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = None
    with tempfile.TemporaryDirectory(prefix='cs-test-') as directory:
        config = Path(directory)/'worker.json'
        config.write_text(json.dumps({'api_url': BASE, 'token': credential['token'], 'node_name': credential['node_name'],
            'pool_id': pool, 'mock': True, 'runner_image': image, 'client_images': [image]})); config.chmod(0o600)
        with (Path(directory)/'worker.log').open('w+') as log:
            def start():
                return subprocess.Popen([sys.executable, '-m', 'worker.client', '--config', str(config)], stdout=log, stderr=log)
            def detail(jid): return request('jobs/'+jid, token=token)
            def submit(name, timeout=60, request_timeout=10, episodes=2):
                body = {'title': 'acceptance-'+name, 'pool_id': pool, 'cases': [{'case_id': name, 'task_name': name, 'episodes': episodes, 'seed': 42}],
                    'client': {'template_id': template, 'endpoint': f'http://{host}:19090/infer', 'timeout_seconds': timeout, 'request_timeout_seconds': request_timeout}}
                return request('batches', body, token)['cases'][0]['job_id']
            def done(jid):
                d = detail(jid)
                return d if d['status'] in ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') else None
            try:
                process = start()
                wait(lambda: any(s['node_name'] == credential['node_name'] for s in request('workers', token=token)))
                good = submit('demo')
                d = wait(lambda: done(good))
                assert d['status'] == 'SUCCEEDED', d
                run = d['attempts'][-1]['id']
                p = request(f'jobs/{good}/attempts/{run}/episodes', token=token)
                assert p['progress']['completed'] == p['progress']['successes'] == 2
                assert request(f'jobs/{good}/attempts/{run}/artifacts', token=token)
                failed = submit('fail')
                assert wait(lambda: done(failed))['status'] == 'FAILED'
                slow = submit('slow', request_timeout=1)
                assert wait(lambda: done(slow))['status'] == 'FAILED'
                timeout = submit('slow', timeout=2)
                assert wait(lambda: done(timeout))['status'] in ('FAILED','TIMED_OUT')
                cancel = submit('slow', episodes=10)
                wait(lambda: detail(cancel)['status'] == 'RUNNING')
                request('jobs/'+cancel+'/cancel', {}, token)
                assert wait(lambda: done(cancel))['status'] == 'CANCELLED'
                recovery = submit('slow', episodes=2)
                wait(lambda: detail(recovery)['status'] == 'RUNNING')
                before = subprocess.check_output(['docker','ps','--filter','label=mini-eval.job='+recovery,'--format','{{.ID}}'],text=True).strip()
                process.kill(); process.wait(timeout=10); process = start()
                recovered = wait(lambda: done(recovery), timeout=100)
                assert recovered['status'] == 'SUCCEEDED', recovered
                assert len(recovered['attempts']) == 1
                print(json.dumps({'normal':d['status'], 'episodes':p['progress'], 'artifact':True,
                    'failure':True,'request_timeout':True,'total_timeout':True,'cancel':True,'worker_recovery':True}))
            except Exception:
                log.flush(); log.seek(0); print(log.read()[-12000:], file=sys.stderr); raise
            finally:
                if process and process.poll() is None:
                    process.terminate(); process.wait(timeout=15)
                server.shutdown();server.server_close()


if __name__ == '__main__': main()
