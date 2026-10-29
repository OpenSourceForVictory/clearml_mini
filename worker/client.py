"""Standard-library-only worker. One instance per GPU (or virtual mock slot)."""
import argparse
import fcntl
import json
import logging
import os
import signal
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

LOG = logging.getLogger('mini-eval-worker')
MAX_LOG_BYTES = 262144


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'API {status}: {message}')


class Client:
    def __init__(self, config):
        self.config = config

    def post(self, path, data=None):
        request = urllib.request.Request(self.config['api_url'].rstrip('/') + '/api/v1/' + path,
            data=json.dumps(data or {}).encode(), method='POST',
            headers={'Authorization': 'Bearer ' + self.config['token'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code, exc.read(2048).decode(errors='replace')) from exc


def docker(*args, timeout=15, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, errors='replace', timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Docker command failed: ' + result.stderr[-1000:])
    return result


def device(config):
    index = int(config.get('gpu_index', 0))
    if config.get('mock', False):
        return {'gpu_index': index, 'gpu_uuid': f'mock:{config["node_name"]}:{index}', 'gpu_model': 'virtual', 'vram_gb': 0}
    result = subprocess.run(['nvidia-smi', f'--id={index}', '--query-gpu=uuid,name,memory.total', '--format=csv,noheader,nounits'],
                            check=True, capture_output=True, text=True, timeout=10)
    identity, model, memory = [x.strip() for x in result.stdout.strip().split(',')]
    return {'gpu_index': index, 'gpu_uuid': identity, 'gpu_model': model, 'vram_gb': int(float(memory) / 1024), 'vram_mb': int(float(memory))}


def telemetry(config):
    if config.get('mock', False):
        return {'free_vram_mb': 0, 'gpu_utilization': 0}
    result = subprocess.run(['nvidia-smi', f"--id={int(config.get('gpu_index', 0))}",
        '--query-gpu=memory.free,utilization.gpu', '--format=csv,noheader,nounits'],
        check=True, capture_output=True, text=True, timeout=5)
    free, utilization = [int(float(x.strip())) for x in result.stdout.strip().split(',')]
    return {'free_vram_mb': free, 'gpu_utilization': utilization}


class Worker:
    def __init__(self, config, state_path):
        self.config = config
        self.api = Client(config)
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else None
        self.slot_id = None
        self.stop_event = threading.Event()
        self.start_lock = threading.Lock()
        self.children = {}
        self.threads = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w') as f:
            os.chmod(temporary, 0o600)
            json.dump(self.state, f)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(self.path)

    def name(self, execution):
        return 'mini-eval-' + str(uuid.UUID(execution))

    def inspect(self, execution):
        name = self.name(execution)
        response = docker('container', 'inspect', name, check=False, timeout=5)
        if response.returncode:
            if 'No such container' in response.stderr or 'No such object' in response.stderr:
                return None
            raise RuntimeError('cannot inspect Docker container: ' + response.stderr[-500:])
        value = json.loads(response.stdout)[0]
        if value['Config']['Labels'].get('mini-eval.execution') != execution:
            raise RuntimeError('container identity mismatch')
        return value

    def stop_container(self, execution):
        container = self.inspect(execution)
        if container and container['State']['Running']:
            docker('kill', self.name(execution), check=False, timeout=10)
            container = self.inspect(execution)
            if container and container['State']['Running']:
                raise RuntimeError('container has not stopped')

    def remove(self, execution):
        self.stop_container(execution)
        if self.inspect(execution):
            docker('rm', self.name(execution))

    def cleanup(self, execution):
        self.stop_container(execution)
        self.api.post(f'worker/executions/{execution}/cleanup')
        self.remove(execution)
        if self.state and self.state['task']['execution_id'] == execution:
            self.state = None
            self.save()

    def create(self, task):
        modules = {'mock': 'runner.mock_eval', 'robotwin-render': 'runner.robotwin_render'}
        if task['benchmark'] not in modules or task['image'] != self.config['runner_image']:
            raise RuntimeError('task benchmark or image is not approved by this worker')
        if task['benchmark'] == 'robotwin-render' and (not task['gpu_uuid'] or task['model_ref'] != 'sapien-render'
                                                       or not 1 <= task['episodes'] <= 100):
            raise RuntimeError('invalid GPU environment check parameters')
        requested = task.get('requested_vram_mb', 0)
        if requested:
            observed = telemetry(self.config)
            if observed['free_vram_mb'] < requested + task.get('vram_reserve_mb', 0):
                raise RuntimeError('insufficient free GPU memory before start; retry when resources are available')
        args = ['create', '--name', self.name(task['execution_id']),
                '--label', 'mini-eval.execution=' + task['execution_id'], '--label', 'mini-eval.slot=' + self.slot_id,
                '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '256', '--cpus', str(task['cpus']), '--memory', str(task['ram_gb'])+'g',
                '--log-driver', 'json-file', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1']
        if task['gpu_uuid']:
            if self.config.get('gpu_runtime') == 'nvidia':
                args.extend(['--runtime', 'nvidia', '--env', 'NVIDIA_VISIBLE_DEVICES=' + task['gpu_uuid'],
                             '--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility'])
            else:
                args.extend(['--gpus', 'device=' + task['gpu_uuid']])
        if task['benchmark'] == 'robotwin-render':
            args.extend(['--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics'])
        if 'mock_episode_sleep' in self.config:
            args.extend(['--env', 'MOCK_EPISODE_SLEEP=' + str(self.config['mock_episode_sleep'])])
        if self.config.get('mock_fail'):
            args.extend(['--env', 'MOCK_FAIL=1'])
        args.extend([task['image'], 'python', '-m', 'runner.supervise', '--timeout', str(task['timeout_seconds']),
                     'python', '-m', modules[task['benchmark']], '--job-id='+task['job_id'], '--model-ref='+task['model_ref'],
                     '--benchmark='+task['benchmark'], '--episodes='+str(task['episodes'])])
        docker(*args, timeout=20)

    def send_logs(self, execution, output):
        # Persist the exact pending chunk before sending; response loss cannot change a retry's body.
        output = output.encode()[:MAX_LOG_BYTES].decode(errors='ignore')
        batches = 0
        while batches < 4 and (self.state.get('pending') or self.state['offset'] < len(output)):
            if not self.state.get('pending'):
                content = output[self.state['offset']:self.state['offset']+2048]
                if not content:
                    break
                self.state['pending'] = {'sequence': self.state['sequence'], 'content': content}
                self.save()
            body = self.state['pending']
            try:
                self.api.post(f'worker/executions/{execution}/logs', body)
            except APIError as exc:
                if exc.status == 413:
                    self.state['offset'] = len(output)
                    self.state['pending'] = None
                    self.save()
                    return True
                raise
            self.state['offset'] += len(body['content'])
            self.state['sequence'] += 1
            self.state['pending'] = None
            self.save()
            batches += 1
        return self.state['offset'] >= len(output)

    def execute(self, task):
        execution = task['execution_id']
        if self.state is None:
            self.state = {'task': task, 'offset': 0, 'sequence': 0, 'created': False, 'pending': None}
            self.save()
        deadline = [time.monotonic() + task['lease_seconds'] - 2]
        done, lease_lost = threading.Event(), threading.Event()

        def watchdog():
            while not done.wait(.5):
                if self.stop_event.is_set() or time.monotonic() >= deadline[0]:
                    lease_lost.set()
                    try:
                        self.stop_container(execution)
                    except Exception:
                        LOG.exception('lease watchdog could not stop container')

        thread = threading.Thread(target=watchdog, daemon=True)
        thread.start()
        try:
            requested_at = time.monotonic()
            initial = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution, 'started': False})
            deadline[0] = requested_at + initial['lease_seconds'] - 2
            if initial['cancel']:
                self.stop_container(execution)
                self.api.post(f'worker/executions/{execution}/complete', {'status': 'CANCELLED'})
                self.remove(execution)
                self.state = None
                self.save()
                return
            with self.start_lock:
                if self.stop_event.is_set() or lease_lost.is_set():
                    raise RuntimeError('worker stopped or lease expired before start')
                container = self.inspect(execution)
                if not container:
                    if self.state['created'] or task['status'] == 'RUNNING':
                        raise RuntimeError('previously created container is missing; refusing duplicate execution')
                    # Crash before create is safe to fail; never recreate an uncertain execution.
                    self.state['created'] = True
                    self.save()
                    self.create(task)
                    container = self.inspect(execution)
                if container['State']['Status'] == 'created':
                    if lease_lost.is_set():
                        raise RuntimeError('lease expired before start')
                    docker('start', self.name(execution), timeout=10)
            while True:
                if self.stop_event.is_set() or lease_lost.is_set():
                    raise RuntimeError('worker stopped or lease expired')
                requested_at = time.monotonic()
                try:
                    reply = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution})
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.5)
                    continue  # Watchdog enforces the existing deadline during transient outages.
                deadline[0] = requested_at + reply['lease_seconds'] - 2
                if reply['cancel']:
                    self.stop_container(execution)
                container = self.inspect(execution)
                if not container:
                    raise RuntimeError('container disappeared')
                output = docker('logs', self.name(execution), timeout=5).stdout
                # stderr may contain the only useful failure message.
                err = docker('logs', '--tail', '30', self.name(execution), timeout=5).stderr
                try:
                    logs_done = self.send_logs(execution, output + ('\n'+err if not container['State']['Running'] and err else ''))
                except (urllib.error.URLError, TimeoutError):
                    continue
                if not container['State']['Running']:
                    if not logs_done:
                        continue
                    result = None
                    for line in reversed(output.splitlines()):
                        try:
                            record = json.loads(line)
                            if isinstance(record, dict) and 'final' in record:
                                result = {k: record['final'][k] for k in ('success_rate', 'episode_count', 'runtime')}
                                break
                        except (ValueError, KeyError, TypeError):
                            continue
                    code = container['State']['ExitCode']
                    status = 'CANCELLED' if reply['cancel'] else ('SUCCEEDED' if code == 0 and result else 'FAILED')
                    body = {'status': status, 'metrics': result if status == 'SUCCEEDED' else None,
                            'error': None if status == 'SUCCEEDED' else (err[-900:] or f'runner exited with code {code}')}
                    self.api.post(f'worker/executions/{execution}/complete', body)
                    self.remove(execution)
                    self.state = None
                    self.save()
                    return
                time.sleep(min(2, max(.2, task['lease_seconds']/4)))
        except APIError as exc:
            self.stop_container(execution)
            if exc.status in (404, 409):
                self.cleanup(execution)
            else:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            # Keep journal and container identity for reconnect; stop before relinquishing lease.
            self.stop_container(execution)
            raise
        except Exception as exc:
            self.stop_container(execution)
            try:
                self.api.post(f'worker/executions/{execution}/complete', {'status': 'FAILED', 'error': str(exc)[:1000]})
            except APIError as api_exc:
                if api_exc.status not in (404, 409):
                    raise
                self.api.post(f'worker/executions/{execution}/cleanup')
            self.remove(execution)
            self.state = None
            self.save()
        finally:
            done.set()
            thread.join(timeout=15)

    def child(self, execution):
        identity = str(uuid.UUID(execution))
        directory = Path(str(self.path) + '.runs')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        child = Worker(self.config, directory / (identity + '.json'))
        child.slot_id = self.slot_id
        child.start_lock = self.start_lock
        return child

    def recover_journals(self):
        # Write the new journal durably before clearing the legacy single-run journal.
        if self.state:
            execution = self.state['task']['execution_id']
            child = self.child(execution)
            if child.state is None:
                child.state = self.state
                child.save()
            self.state = None
            self.save()
        directory = Path(str(self.path) + '.runs')
        if directory.exists():
            for path in directory.glob('*.json'):
                child = self.child(path.stem)
                if child.state:
                    self.children[path.stem] = child

    def launch(self, child, task):
        execution = task['execution_id']
        if self.threads.get(execution) and self.threads[execution].is_alive():
            return
        self.children[execution] = child
        def execute():
            try:
                child.execute(task)
            except Exception:
                LOG.exception('execution interrupted; journal retained for recovery')
        thread = threading.Thread(target=execute, daemon=True, name='execution-' + execution)
        self.threads[execution] = thread
        thread.start()

    def shutdown(self):
        for child in self.children.values():
            child.stop_event.set()
        # Stop each container independently even if its API or execution thread is blocked.
        for execution, child in self.children.items():
            try:
                child.stop_container(execution)
            except Exception:
                LOG.exception('shutdown could not stop container; journal retained')
        for thread in self.threads.values():
            thread.join(timeout=20)

    def run(self, once=False):
        hardware = device(self.config)
        registration = self.api.post('worker/register', {'pool_id': self.config['pool_id'],
            **hardware, 'protocol_version': 2})
        self.slot_id = registration['slot_id']
        self.slot_lock = open('/tmp/mini-eval-slot-' + self.slot_id + '.lock', 'a')
        fcntl.flock(self.slot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.recover_journals()
        had_task = bool(self.children)
        while True:
            try:
                for execution, child in list(self.children.items()):
                    if self.threads.get(execution) and self.threads[execution].is_alive():
                        continue
                    if child.state:
                        self.launch(child, child.state['task'])
                    else:
                        self.children.pop(execution)
                        self.threads.pop(execution, None)
                        child.path.unlink(missing_ok=True)
                if once and had_task and not self.children:
                    return
                body = {'slot_id': self.slot_id}
                try:
                    body['telemetry'] = telemetry(self.config)
                except (subprocess.SubprocessError, OSError, ValueError):
                    body['telemetry_failed'] = True
                    LOG.warning('GPU telemetry unavailable; shared admission will wait')
                self.api.post('worker/heartbeat', body)
                if once and had_task:
                    time.sleep(2)
                    continue
                response = self.api.post(f'worker/slots/{self.slot_id}/claim',
                    {'known_execution_ids': list(self.children)})
                for execution in response['cleanup']:
                    # An active thread owns its cleanup; signal it rather than racing its journal.
                    if execution in self.children:
                        self.children[execution].stop_event.set()
                    else:
                        self.child(execution).cleanup(execution)
                if response['task'] and not (once and had_task):
                    task = response['task']
                    child = self.child(task['execution_id'])
                    self.launch(child, task)
                    had_task = True
            except (APIError, urllib.error.URLError, TimeoutError, OSError, RuntimeError):
                LOG.exception('worker iteration failed; retrying')
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    path = Path(args.config).resolve()
    config = json.loads(path.read_text())
    state_path = config.get('state_path', str(path) + '.state')
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    with open(str(state_path)+'.lock', 'a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        worker = Worker(config, state_path)
        def stop_signal(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop_signal)
        try:
            worker.run(args.once)
        except KeyboardInterrupt:
            pass
        finally:
            worker.shutdown()


if __name__ == '__main__':
    main()
