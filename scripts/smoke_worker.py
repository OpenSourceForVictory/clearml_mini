"""Real API + PostgreSQL + Docker worker smoke. Use an isolated test deployment."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

BASE = os.getenv('EVAL_API_URL', 'http://127.0.0.1:18080')
ROOT = Path(__file__).resolve().parents[1]
TOKEN = None


def api(method, path, body=None):
    headers = {'Content-Type': 'application/json'}
    if TOKEN:
        headers['Authorization'] = 'Bearer ' + TOKEN
    req = urllib.request.Request(BASE + '/api/v1/' + path, method=method, headers=headers,
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def wait(job, statuses, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = api('GET', 'jobs/' + job)
        if value['status'] in statuses:
            return value
        if value['status'] in ('SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'):
            raise AssertionError(value)
        time.sleep(.25)
    raise TimeoutError(f'job {job} did not reach {statuses}')


def main():
    global TOKEN
    TOKEN = api('POST', 'auth/login', {'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']})['access_token']
    suffix = uuid.uuid4().hex[:10]
    gpu_index = os.getenv('SMOKE_GPU_INDEX')
    from worker.client import device
    hardware = device({'mock': gpu_index is None, 'gpu_index': int(gpu_index or 0), 'node_name': 'smoke-'+suffix})
    pool = api('POST', 'admin/pools', {'name': 'smoke-'+suffix, 'gpu_model': hardware['gpu_model']})['id']
    node = api('POST', 'admin/worker-credentials', {'node_name': 'smoke-'+suffix})
    config = {'api_url': BASE, 'token': node['token'], 'node_name': node['node_name'], 'pool_id': pool,
              'mock': gpu_index is None, 'gpu_index': int(gpu_index or 0),
              'runner_image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:dev'), 'mock_episode_sleep': .3}
    if os.getenv('SMOKE_GPU_RUNTIME'):
        config['gpu_runtime'] = os.environ['SMOKE_GPU_RUNTIME']
    process = None
    with tempfile.TemporaryDirectory(prefix='mini-eval-smoke-') as directory:
        path = Path(directory) / 'worker.json'
        log = open(Path(directory) / 'worker.log', 'w+')
        def start():
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            return subprocess.Popen([sys.executable, '-m', 'worker.client', '--config', str(path)], cwd=ROOT, stdout=log, stderr=log)
        def stop():
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        def submit(episodes):
            return api('POST', 'jobs', {'title': 'worker smoke', 'benchmark': 'mock', 'model_ref': 'demo', 'episodes': episodes, 'pool_id': pool})['id']
        try:
            process = start()
            job = submit(20)
            wait(job, {'RUNNING'})
            state = json.loads(Path(str(path)+'.state').read_text())
            execution = state['task']['execution_id']
            if gpu_index is not None:
                container = json.loads(subprocess.check_output(['docker', 'inspect', 'mini-eval-'+execution]))[0]
                if config.get('gpu_runtime') == 'nvidia':
                    assert container['HostConfig']['Runtime'] == 'nvidia'
                    assert 'NVIDIA_VISIBLE_DEVICES='+hardware['gpu_uuid'] in container['Config']['Env']
                else:
                    requests = container['HostConfig']['DeviceRequests']
                    assert any(hardware['gpu_uuid'] in (r.get('DeviceIDs') or []) for r in requests)
                observed = subprocess.check_output(['docker', 'exec', 'mini-eval-'+execution,
                    'nvidia-smi', '--query-gpu=uuid', '--format=csv,noheader'], text=True).strip()
                assert observed == hardware['gpu_uuid'], observed
                print('PASS: container sees only the selected physical GPU', flush=True)
            process.kill()  # SIGKILL simulates a crash while the Docker container remains alive.
            process.wait(timeout=10)
            process = start()
            result = wait(job, {'SUCCEEDED'})
            assert len(result['attempts']) == 1
            assert result['attempts'][0]['metrics']['episode_count'] == 20
            logs = api('GET', f'jobs/{job}/logs?limit=100')['chunks']
            assert 'episode' in ''.join(x['content'] for x in logs)
            print('PASS: real Docker execution, metrics, logs and worker restart', flush=True)
            cancelled = submit(100)
            wait(cancelled, {'RUNNING'})
            api('POST', f'jobs/{cancelled}/cancel')
            wait(cancelled, {'CANCELLED'})
            print('PASS: running container cancellation', flush=True)
            stop()
            config['mock_fail'] = True
            process = start()
            failed = submit(1)
            wait(failed, {'FAILED'})
            stop()
            config['mock_fail'] = False
            process = start()
            retry = api('POST', f'jobs/{failed}/retry')
            assert retry['attempt'] == 2
            retried = wait(failed, {'SUCCEEDED'})
            assert [r['status'] for r in retried['attempts']] == ['FAILED', 'SUCCEEDED']
            print('PASS: injected runner failure and new retry attempt', flush=True)
            lost = submit(100)
            wait(lost, {'RUNNING'})
            process.kill()
            process.wait(timeout=10)
            timed_out = wait(lost, {'TIMED_OUT'}, timeout=90)
            assert not timed_out['attempts'][-1]['cleanup_confirmed']
            try:
                api('POST', f'jobs/{lost}/retry')
                raise AssertionError('unsafe retry was allowed')
            except urllib.error.HTTPError as exc:
                assert exc.code == 409
            process = start()
            deadline = time.monotonic() + 30
            while not api('GET', 'jobs/'+lost)['attempts'][-1]['cleanup_confirmed']:
                if time.monotonic() > deadline:
                    raise TimeoutError('worker did not confirm cleanup')
                time.sleep(.5)
            assert api('POST', f'jobs/{lost}/retry')['attempt'] == 2
            api('POST', f'jobs/{lost}/cancel')
            wait(lost, {'CANCELLED'})
            print('PASS: lost worker timeout, quarantine, stale execution cleanup and safe retry', flush=True)
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-8000:], file=sys.stderr)
            raise
        finally:
            stop()
            state_path = Path(str(path)+'.state')
            if state_path.exists():
                state = json.loads(state_path.read_text())
                if state:
                    from worker.client import Worker
                    client = Worker(config, state_path)
                    client.cleanup(state['task']['execution_id'])
            api('PATCH', 'admin/nodes/' + node['id'], {'enabled': False})
            log.close()


if __name__ == '__main__':
    main()
