"""Exercise three real worker processes as virtual single-slot and two-slot nodes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from scripts import smoke_worker as smoke


def main():
    smoke.TOKEN = smoke.api('POST', 'auth/login', {'username': os.environ['ADMIN_USERNAME'], 'password': os.environ['ADMIN_PASSWORD']})['access_token']
    admin = smoke.TOKEN
    suffix = uuid.uuid4().hex[:8]
    pool = smoke.api('POST', 'admin/pools', {'name': 'farm-'+suffix, 'gpu_model': 'virtual'})['id']
    accounts = []
    for i in range(3):
        name = f'farm-{suffix}-{i}'
        smoke.api('POST', 'admin/users', {'username': name, 'password': 'temporary-farm-password'})
        accounts.append(smoke.api('POST', 'auth/login', {'username': name, 'password': 'temporary-farm-password'})['access_token'])
    jobs, owners = [], {}
    for i, token in enumerate(accounts):
        smoke.TOKEN = token
        for _ in range(5):
            jid = smoke.api('POST', 'jobs', {'title': 'virtual farm acceptance', 'benchmark': 'mock', 'model_ref': 'demo', 'episodes': 5, 'pool_id': pool})['id']
            jobs.append(jid)
            owners[jid] = i
    smoke.TOKEN = admin
    nodes, processes, states = [], [], []
    with tempfile.TemporaryDirectory(prefix='mini-eval-farm-') as directory:
        try:
            for label, count in [('single', 1), ('multi', 2)]:
                node = smoke.api('POST', 'admin/worker-credentials', {'node_name': f'{label}-{suffix}', 'max_concurrent': count})
                nodes.append(node)
                for index in range(count):
                    path = Path(directory) / f'{label}-{index}.json'
                    config = {'api_url': smoke.BASE, 'token': node['token'], 'node_name': node['node_name'], 'pool_id': pool,
                              'mock': True, 'gpu_index': index, 'runner_image': os.getenv('RUNNER_IMAGE', 'mini-eval-runner:dev'), 'mock_episode_sleep': .2}
                    path.write_text(json.dumps(config))
                    path.chmod(0o600)
                    states.append((path, config))
                    processes.append(subprocess.Popen([sys.executable, '-m', 'worker.client', '--config', str(path)], cwd=smoke.ROOT,
                                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            peak = 0
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                snapshot = [j for j in smoke.api('GET', 'admin/jobs?page_size=100') if j['id'] in owners]
                active = [j for j in snapshot if j['status'] in ('RESERVED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED')]
                assert len(active) <= 2
                peak = max(peak, len(active))
                assert all(len([j for j in active if owners[j['id']] == owner]) <= 2 for owner in range(3))
                assert all(j['status'] not in ('FAILED', 'TIMED_OUT') for j in snapshot), snapshot
                if len(snapshot) == 15 and all(j['status'] == 'SUCCEEDED' for j in snapshot):
                    assert all(len(j['attempts']) == 1 for j in snapshot)
                    assert peak == 2
                    print('PASS: 3 users × 5 jobs; virtual nodes with 1+2 slots; peak concurrency 2; one attempt per job')
                    break
                time.sleep(.25)
            else:
                raise TimeoutError('virtual farm did not finish')
        finally:
            for p in processes:
                p.terminate()
            for p in processes:
                p.wait(timeout=10)
            from worker.client import Worker
            for path, config in states:
                state_path = Path(str(path)+'.state')
                if state_path.exists() and json.loads(state_path.read_text()):
                    worker = Worker(config, state_path)
                    worker.cleanup(worker.state['task']['execution_id'])
            for node in nodes:
                smoke.api('PATCH', 'admin/nodes/'+node['id'], {'enabled': False})


if __name__ == '__main__':
    main()
