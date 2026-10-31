"""Standard-library-only worker. One instance per GPU (or virtual mock slot)."""
import argparse
import base64
import tempfile
import fcntl
import json
import logging
import os
import signal
import shutil
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from runner.cs_contract import INPUT_PATH, VERSION, endpoint_parts
from .artifacts import read_artifacts
from .workspace import prepare, execution_dirs, read_events, disk_status, validate_storage
from .storage import Storage
from .image_runtime import Runtime
from runner.image_runtime import validate as validate_runtime, ADAPTER, WORK, OUTPUT

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
    def __init__(self, config, state_path, snapshot=None, runtime_snapshot=None):
        self.config = dict(config)
        self.api = Client(self.config)
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else None
        if snapshot is None:
            if not Path(str(self.path) + '.storage.json').exists():
                root = prepare(self.config, str(self.path.resolve().parent/'workspace'))
                self.config['workspace_root'] = str(root)
            self.storage = Storage(self.config, self.path)
            snapshot = self.storage.snapshot()
        else:
            self.storage = None
        if self.state and self.state.get('storage'):
            snapshot = self.state['storage']
        self.runtime = Runtime(self.config, self.path) if self.storage is not None else None
        self.runtime_snapshot = (self.state or {}).get("runtime") or runtime_snapshot or (self.runtime.snapshot() if self.runtime else {"revision": 0, "config": {"images": self.config.get("client_images", []), "assets": self.config.get("asset_mappings", {})}})
        self.config["client_images"] = self.runtime_snapshot["config"]["images"]
        self.storage_snapshot = snapshot
        self.config.update(snapshot['config'])
        self.workspace = Path(self.config['workspace_root'])
        if self.state and 'storage' not in self.state:
            self.state['storage'] = snapshot
            self.save()
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
        if task.get('storage_revision', 0) != self.storage_snapshot['revision']:
            raise RuntimeError('task storage revision differs from execution snapshot')
        validate_storage(self.config, self.config.get('_supervisor_state', self.path), self.config.get('_config_path'))
        disk = disk_status(self.workspace, self.config)
        if disk['free_mb'] < disk['min_free_mb']:
            raise RuntimeError('insufficient disk space in worker workspace before start')
        modules = {'mock': 'runner.mock_eval', 'robotwin-render': 'runner.robotwin_render', 'cs-client': 'runner.cs_entry'}
        cs = task['benchmark'] == 'cs-client'
        approved = task['image'] in self.config.get('client_images', []) if cs else task['image'] == self.config['runner_image']
        if task['benchmark'] not in modules or not approved:
            raise RuntimeError('task benchmark or image is not approved by this worker')
        if task['benchmark'] == 'robotwin-render' and (not task['gpu_uuid'] or task['model_ref'] != 'sapien-render'
                                                       or not 1 <= task['episodes'] <= 100):
            raise RuntimeError('invalid GPU environment check parameters')
        if cs:
            spec = task['client_spec']
            if spec['contract_version'] != VERSION or spec['template']['image'] != task['image']:
                raise RuntimeError('invalid CS client contract')
            endpoint_parts(spec['endpoint'])
        runtime = validate_runtime(spec['template']['runtime'], spec['template']['command']) if cs and spec['template'].get('runtime') is not None else None
        if runtime and task.get('runtime_revision', 0) != self.runtime_snapshot['revision']:
            raise RuntimeError('task runtime revision differs from execution snapshot')
        requested = task.get('requested_vram_mb', 0)
        if requested:
            observed = telemetry(self.config)
            if observed['free_vram_mb'] < requested + task.get('vram_reserve_mb', 0):
                raise RuntimeError('insufficient free GPU memory before start; retry when resources are available')
        args = ['create', '--name', self.name(task['execution_id']),
                '--label', 'mini-eval.execution=' + task['execution_id'], '--label', 'mini-eval.slot=' + self.slot_id,
                '--network', 'bridge' if cs else 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--pids-limit', '256', '--cpus', str(task['cpus']), '--memory', str(task['ram_gb'])+'g',
                '--log-driver', 'json-file', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1']
        if cs:
            directory = execution_dirs(self.workspace, task['execution_id'])
            for source, target, readonly in [
                (Path(self.config['assets_root']), '/opt/mini-eval/assets', True),
                (Path(self.config['cache_root']), '/opt/mini-eval/cache', True),
                (directory/'work', '/tmp/mini-eval-work', False),
                (directory/'output', '/tmp/mini-eval-output', False)]:
                args.extend(['--mount', f'type=bind,source={source},target={target}'+(',readonly' if readonly else '')])
            args.extend(['--env', 'MINI_EVAL_WORK_DIR=/tmp/mini-eval-work',
                         '--env', 'MINI_EVAL_ASSETS_DIR=/opt/mini-eval/assets',
                         '--env', 'MINI_EVAL_CACHE_DIR=/opt/mini-eval/cache'])
        if runtime:
            aliases = [(directory/'work', runtime['work_dir'], False), (directory/'output', runtime['output_dir'], False)]
            for name, target in runtime['assets'].items():
                host = self.runtime_snapshot['config']['assets'].get(name)
                if host is None:
                    raise RuntimeError('logical asset is not configured: '+name)
                validate_storage({**self.config, 'assets_root': host, 'cache_root': host}, self.config.get('_supervisor_state', self.path), self.config.get('_config_path'))
                aliases.append((host, target, True))
            for source, target, readonly in aliases:
                if target not in (WORK, OUTPUT):
                    args.extend(['--mount', f'type=bind,source={source},target={target}'+(',readonly' if readonly else '')])
            args.extend(['--pull', 'never', '--entrypoint', runtime['interpreter'], '--env', 'PYTHONPATH='+ADAPTER])
        if task['gpu_uuid']:
            if self.config.get('gpu_runtime') == 'nvidia':
                args.extend(['--runtime', 'nvidia', '--env', 'NVIDIA_VISIBLE_DEVICES=' + task['gpu_uuid'],
                             '--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility'])
            else:
                args.extend(['--gpus', 'device=' + task['gpu_uuid']])
        if task['benchmark'] in ('robotwin-render', 'cs-client'):
            args.extend(['--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics'])
        if 'mock_episode_sleep' in self.config:
            args.extend(['--env', 'MOCK_EPISODE_SLEEP=' + str(self.config['mock_episode_sleep'])])
        if self.config.get('mock_fail'):
            args.extend(['--env', 'MOCK_FAIL=1'])
        command = ['python', '-m', modules[task['benchmark']]]
        if not cs:
            command += ['--job-id='+task['job_id'], '--model-ref='+task['model_ref'],
                        '--benchmark='+task['benchmark'], '--episodes='+str(task['episodes'])]
        if runtime:
            command[0] = runtime['interpreter']
        args.extend([task['image'], *([] if runtime else ['python']), '-m', 'runner.supervise', '--timeout', str(task['timeout_seconds']), *command])
        docker(*args, timeout=20)
        if runtime:
            with tempfile.TemporaryDirectory(prefix='eval-adapter-') as temporary:
                root = Path(temporary)/'mini-eval-runtime'
                shutil.copytree(Path(__file__).resolve().parent.parent/'runner', root/'runner', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                for path in [root, *root.rglob('*')]:
                    path.chmod(0o755 if path.is_dir() else 0o644)
                docker('cp', str(root), self.name(task['execution_id']) + ':/tmp/')
        if cs:
            # No bind mount or shell expansion. The file lives inside the container.
            config = {**task['client_spec'], 'job_id': task['job_id'], 'execution_id': task['execution_id'],
                      'episodes': task['episodes'], 'timeout_seconds': task['timeout_seconds']}
            (execution_dirs(self.workspace, task['execution_id'])/'input.json').write_text(json.dumps(config))
            with tempfile.TemporaryDirectory(prefix='eval-input-') as directory:
                path = Path(directory)/'input.json'
                path.write_text(json.dumps(config)); path.chmod(0o644)
                docker('cp', str(path), self.name(task['execution_id']) + ':' + INPUT_PATH)

    def upload_artifacts(self, task, deadline=None):
        execution = task['execution_id']
        for name, data in read_artifacts(self.name(execution)):
            requested = time.monotonic()
            reply = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution})
            if deadline is not None:
                deadline[0] = requested + reply['lease_seconds'] - 2
            self.api.post(f'worker/executions/{execution}/artifacts',
                          {'name': name, 'content_base64': base64.b64encode(data).decode()})

    def send_episode_journal(self, execution):
        offset = self.state.get('episode_file_offset', 0)
        path = execution_dirs(self.workspace, execution)/'work'/'episodes.jsonl'
        data = read_events(path, offset)
        sent = 0
        for line in data.splitlines(keepends=True):
            if not line.endswith(b'\n'):
                if len(line) > 16384:
                    raise RuntimeError('episode event exceeds size limit')
                break
            if sent >= 16:
                return False
            if len(line) > 16384:
                raise RuntimeError('episode event exceeds size limit')
            event = json.loads(line)
            self.api.post(f'worker/executions/{execution}/episodes', event)
            offset += len(line); sent += 1
            self.state['episode_file_offset'] = offset
            self.save()
        return len(data) < 1024*1024

    def send_episodes(self, execution, output):
        from runner.sdk import EVENT_PREFIX
        # Separate durable cursor: log truncation must not discard evaluation results.
        offset = self.state.get('episode_offset', 0)
        tail = output[offset:]
        sent = 0
        for line in tail.splitlines(keepends=True):
            if sent >= 16:
                return False
            if not line.endswith('\n'):
                break
            if line.startswith(EVENT_PREFIX):
                event = json.loads(line[len(EVENT_PREFIX):])
                self.api.post(f'worker/executions/{execution}/episodes', event)
                sent += 1
            offset += len(line)
            self.state['episode_offset'] = offset
            self.save()
        return True

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
            self.state = {'runtime': self.runtime_snapshot, 'storage': self.storage_snapshot, 'task': task, 'offset': 0, 'sequence': 0, 'created': False, 'pending': None}
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
                    if task.get('benchmark') == 'cs-client':
                        if task['client_spec']['template'].get('episode_reporting'):
                            if not self.send_episode_journal(execution):
                                continue
                        elif not self.send_episodes(execution, output):
                            continue
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
                    if task.get('benchmark') == 'cs-client':
                        self.upload_artifacts(task, deadline)
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
            elif exc.status in (400, 413, 422):
                # Replaying an invalid event/result cannot succeed. Release capacity
                # through a failed completion, while retaining the journal on outages.
                try:
                    self.api.post(f'worker/executions/{execution}/complete',
                        {'status': 'FAILED', 'error': ('execution data rejected: '+str(exc))[:1000]})
                except APIError as failure:
                    if failure.status not in (404, 409):
                        raise
                    self.api.post(f'worker/executions/{execution}/cleanup')
                self.remove(execution)
                self.state = None
                self.save()
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
        path = directory / (identity + '.json')
        config = {**self.config, '_supervisor_state': str(self.path)}
        snapshot = self.storage_snapshot
        if path.exists() and json.loads(path.read_text()):
            # Pre-v4 journals must retain their original bootstrap paths.
            snapshot = {'config': self.storage.record['bootstrap'], 'revision': 0} if self.storage else snapshot
        child = Worker(config, path, snapshot=snapshot, runtime_snapshot=self.runtime_snapshot)
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
        if self.runtime:
            self.runtime.stopping.set()
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
        if self.runtime:
            self.runtime.close()

    def run(self, once=False):
        hardware = device(self.config)
        registration = self.api.post('worker/register', {'pool_id': self.config.get('pool_id'),
            **hardware, 'protocol_version': 5, 'client_images': self.config.get('client_images', [])})
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
                body = {'slot_id': self.slot_id, 'storage': self.storage.report(), 'runtime': self.runtime.report()}
                try:
                    body['disk'] = disk_status(self.workspace, self.config)
                except (OSError, ValueError):
                    body['disk'] = {'free_mb': 0, 'min_free_mb': 1}
                try:
                    body['telemetry'] = telemetry(self.config)
                except (subprocess.SubprocessError, OSError, ValueError):
                    body['telemetry_failed'] = True
                    LOG.warning('GPU telemetry unavailable; shared admission will wait')
                reply = self.api.post('worker/heartbeat', body)
                if 'storage' in reply:
                    self.storage.update(reply['storage'], self.slot_id,
                        bool(self.children) or reply.get('storage_busy', True), docker)
                    self.storage_snapshot = self.storage.snapshot()
                    self.config.update(self.storage_snapshot['config'])
                    self.workspace = Path(self.config['workspace_root'])
                if 'runtime' in reply:
                    self.runtime.update(reply['runtime'], bool(self.children) or reply.get('storage_busy', True), dict(self.config), docker)
                    self.runtime_snapshot = self.runtime.snapshot()
                    self.config['client_images'] = self.runtime_snapshot['config']['images']
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
    config['_config_path'] = str(path)
    root = prepare(config, str(path.parent/('workspace-'+path.stem)))
    config['workspace_root'] = str(root)
    state_path = config.get('state_path', str(root/'state'/'worker.json'))
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
