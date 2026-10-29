"""Isolated real GPU acceptance: sharing, queued third task, restart and independent cancellation.

Run with Docker access against a disposable API/DB, never a production deployment.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

BASE = os.getenv('EVAL_API_URL', 'http://127.0.0.1:18080')


def request(path, body=None, token=None, method=None):
    req = urllib.request.Request(BASE + '/api/v1/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer '+token} if token else {})},
        method=method or ('POST' if body is not None else 'GET'))
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait(check, timeout=100):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.5)
    raise AssertionError('timed out waiting for acceptance condition')


def main():
    from worker.client import device
    token = request('auth/login', {'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']})['access_token']
    config = {'gpu_index': int(os.getenv('SMOKE_GPU_INDEX', '0')), 'mock': False}
    hardware = device(config)
    suffix = uuid.uuid4().hex[:8]
    pool = request('admin/pools', {'name': 'shared-test-'+suffix, 'allow_shared': True,
        'max_tasks_per_gpu': 2, 'vram_reserve_gb': 2}, token)['id']
    node = request('admin/worker-credentials', {'node_name': 'shared-test-'+suffix,
        'max_concurrent': 3, 'cpu_capacity': 4, 'ram_gb_capacity': 8}, token)
    config.update({'api_url': BASE, 'pool_id': pool, 'token': node['token'], 'node_name': node['node_name'],
        'runner_image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:robotwin-render'),
        'gpu_runtime': os.getenv('SMOKE_GPU_RUNTIME', 'nvidia'), 'mock_episode_sleep': .5})
    process = None
    with tempfile.TemporaryDirectory(prefix='shared-gpu-test-') as directory:
        path = Path(directory)/'worker.json'
        path.write_text(json.dumps(config)); path.chmod(0o600)
        with (Path(directory)/'worker.log').open('w+') as log:
            def start():
                return subprocess.Popen([sys.executable, '-m', 'worker.client', '--config', str(path)], stdout=log, stderr=log)
            def detail(jid):
                return request('jobs/'+jid, token=token)
            def containers():
                result = subprocess.run(['docker', 'ps', '--filter', 'label=mini-eval.slot='+slot,
                    '--format', '{{.Names}} {{.ID}}'], check=True, capture_output=True, text=True)
                return dict(line.split() for line in result.stdout.splitlines())
            try:
                process = start()
                slot = wait(lambda: next((s['id'] for s in request('workers', token=token)
                    if s['node_name'] == node['node_name'] and s['telemetry_fresh']), None))
                jobs = [request('jobs', {'title': 'sharing-acceptance-'+suffix, 'benchmark': 'mock', 'model_ref': 'm',
                    'episodes': 60, 'pool_id': pool, 'gpu_mode': 'shared', 'requested_vram_gb': 8}, token)['id'] for _ in range(3)]
                wait(lambda: all(detail(j)['status'] == 'RUNNING' for j in jobs[:2]))
                assert detail(jobs[2])['status'] == 'PENDING'
                before = containers(); assert len(before) == 2, before
                for name in before:
                    visible = subprocess.run(['docker', 'exec', name, 'nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'],
                        check=True, capture_output=True, text=True).stdout.strip()
                    assert visible == hardware['gpu_uuid'], visible
                process.kill(); process.wait(timeout=10)
                process = start()
                wait(lambda: all(detail(j)['status'] == 'RUNNING' for j in jobs[:2]))
                time.sleep(3)
                assert containers() == before, 'restart recreated containers'
                request('jobs/'+jobs[0]+'/cancel', {}, token)
                wait(lambda: detail(jobs[0])['status'] == 'CANCELLED')
                assert detail(jobs[1])['status'] == 'RUNNING', 'cancelling peer stopped unrelated execution'
                peak = 2
                deadline = time.monotonic()+100
                while time.monotonic()<deadline:
                    statuses = [detail(j)['status'] for j in jobs]
                    peak = max(peak, len(containers()))
                    assert peak <= 2
                    if statuses == ['CANCELLED', 'SUCCEEDED', 'SUCCEEDED']:
                        break
                    assert not any(s in ('FAILED', 'TIMED_OUT') for s in statuses), statuses
                    time.sleep(1)
                else:
                    raise AssertionError(statuses)
                for j in jobs:
                    assert len(detail(j)['attempts']) == 1
                    assert detail(j)['attempts'][0]['cleanup_confirmed']
                render = request('jobs', {'title': 'shared-render-'+suffix, 'benchmark': 'robotwin-render',
                    'model_ref': 'sapien-render', 'episodes': 3, 'pool_id': pool,
                    'gpu_mode': 'shared', 'requested_vram_gb': 4}, token)['id']
                wait(lambda: detail(render)['status'] in ('SUCCEEDED', 'FAILED', 'TIMED_OUT'))
                assert detail(render)['status'] == 'SUCCEEDED', detail(render)
                print(json.dumps({'status': 'passed', 'gpu_uuid': hardware['gpu_uuid'], 'peak_concurrent': peak,
                    'restart_preserved_container_ids': True, 'jobs': jobs, 'render_job': render}, indent=2), flush=True)
            except Exception:
                log.flush(); log.seek(0); print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                if process and process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    process.wait(timeout=50)


if __name__ == '__main__':
    main()
