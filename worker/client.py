"""Standard-library-only worker. One instance per GPU (or virtual mock slot)."""
import argparse
import base64
import tempfile
import fcntl
import hashlib
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
from .resource_limits import execution_pids_limit
from .workspace import prepare, execution_dirs, read_events, disk_status, validate_storage
from .storage import Storage
from .retention import Retention
from .backpressure import APIError, Backoff, transient
from .image_runtime import Runtime
from .host_environments import HostEnvironments
from .host_backend import HostBackend
from .services import is_service, check_bind, health as service_health
from runner.image_runtime import validate as validate_runtime, ADAPTER, WORK, OUTPUT

LOG = logging.getLogger('mini-eval-worker')
MAX_LOG_BYTES = 262144


class StartupCancelled(Exception):
    """Control plane cancelled the execution during startup preparation."""


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
            raise APIError(exc.code, exc.read(2048).decode(errors='replace'),
                           exc.headers.get('Retry-After') if exc.headers else None) from exc


def docker(*args, timeout=15, check=True):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, errors='replace', timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Docker command failed: ' + result.stderr[-1000:])
    return result


def device(config):
    index = int(config.get('gpu_index', 0))
    if config.get('mock', False):
        return {'gpu_index': index, 'gpu_uuid': f'mock:{config["node_name"]}:{index}', 'gpu_model': 'virtual', 'vram_gb': 0}
    if config.get('accelerator_type')=='npu':
        from .accelerators import ascend_sample
        d=ascend_sample(config)
        return {'accelerator_type':'npu','gpu_index':index,'gpu_uuid':d['identity'],'gpu_model':d['model'],'vram_gb':int(d['total_mb']/1024),'vram_mb':d['total_mb']}
    result = subprocess.run(['nvidia-smi', f'--id={index}', '--query-gpu=uuid,name,memory.total', '--format=csv,noheader,nounits'],
                            check=True, capture_output=True, text=True, timeout=10)
    identity, model, memory = [x.strip() for x in result.stdout.strip().split(',')]
    return {'gpu_index': index, 'gpu_uuid': identity, 'gpu_model': model, 'vram_gb': int(float(memory) / 1024), 'vram_mb': int(float(memory))}


def telemetry(config):
    if config.get('mock', False):
        return {'free_vram_mb': 0, 'gpu_utilization': 0}
    if config.get('accelerator_type')=='npu':
        from .accelerators import ascend_sample
        d=ascend_sample(config);return {'free_vram_mb':d['free_mb'],'gpu_utilization':d['utilization']}
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
        self.config.pop('artifact_root',None)
        self.config.update(snapshot['config'])
        self.workspace = Path(self.config['workspace_root'])
        if self.state and 'storage' not in self.state:
            self.state['storage'] = snapshot
            self.save()
        self.host_environments = HostEnvironments(self.path,self.config) if self.storage else None
        self.slot_id = None
        self.stop_event = threading.Event()
        self.start_lock = threading.Lock()
        self.children = {}
        self.threads = {}
        self.retention = None
        self.control = None
        self.process_logs = None

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

    def host_mode(self):
        return bool(self.state and (self.state['task'].get('client_spec') or {}).get('template', {}).get('execution_mode') == 'host')

    def inspect(self, execution):
        if self.host_mode(): return HostBackend(self).inspect(execution)
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
        if self.host_mode(): return HostBackend(self).stop(execution)
        container = self.inspect(execution)
        if container and container['State']['Running']:
            docker('kill', self.name(execution), check=False, timeout=10)
            container = self.inspect(execution)
            if container and container['State']['Running']:
                raise RuntimeError('container has not stopped')

    def remove(self, execution):
        if self.host_mode(): return HostBackend(self).remove(execution)
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

    def register_shared_artifacts(self, task, *, retry_transient=False):
        if task.get('artifact_transport') != 'shared':
            return
        try:
            from .artifact_layout import location
            root=location(getattr(self,"config",{}),task)
            body={'output_root':str(root or self.workspace/'runs'/task['execution_id']/'output')}
            if root:body['layout']='batch-v1'
            self.api.post(f"worker/executions/{task['execution_id']}/shared-artifacts",body)
        except Exception as exc:
            if retry_transient and transient(exc):
                raise
            logging.getLogger(__name__).warning('shared artifact directory registration deferred', exc_info=False)

    def create(self, task):
        self.register_shared_artifacts(task)
        pids=execution_pids_limit(self.config)
        if task.get('accelerator_type','gpu')=='gpu' and task.get('gpu_mode')=='shared' and isinstance(self.config.get('gpu_runtime'),dict) and self.config['gpu_runtime'].get('allow_shared') is not True:
            raise RuntimeError('GPU shared execution is not approved')
        if is_service(task): check_bind(task['client_spec']['service'])
        if self.host_mode(): return HostBackend(self).create(task)
        if task.get('storage_revision', 0) != self.storage_snapshot['revision']:
            raise RuntimeError('task storage revision differs from execution snapshot')
        validate_storage(self.config, self.config.get('_supervisor_state', self.path), self.config.get('_config_path'))
        disk = disk_status(self.workspace, self.config)
        if disk['free_mb'] < disk['min_free_mb']:
            raise RuntimeError('insufficient disk space in worker workspace before start')
        modules = {'mock': 'runner.mock_eval', 'robotwin-render': 'runner.robotwin_render', 'cs-client': 'runner.cs_entry', 'inference-service': 'runner.service_entry'}
        cs = task['benchmark'] in ('cs-client', 'inference-service')
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
            if spec.get('service_mode')!='none':endpoint_parts(spec['endpoint'])
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
                '--log-driver', 'json-file', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1']
        args.extend(['--pids-limit','-1' if pids is None else str(pids)])
        if task['cpus']:args.extend(['--cpus',str(task['cpus'])])
        if task['ram_gb']:args.extend(['--memory',str(task['ram_gb'])+'g'])
        if is_service(task):
            service = task['client_spec']['service']
            args.extend(['--publish', f"{service['address']}:{service['port']}:{service['container_port']}"])
            if service.get('readiness',{}).get('port','business')!='business':
                args.extend(['--publish',f"{service['address']}:{service['health_port']}:{service['readiness']['port']}"])
        process_log_config=None
        if cs:
            directory = execution_dirs(self.workspace, task['execution_id'])
            from .artifact_layout import prepare as prepare_artifacts, output, runner_config
            artifact_root=prepare_artifacts(self.config,task,container=True)
            artifact_output=output(self.config,task,directory)
            self.register_shared_artifacts(task)
            if artifact_root:
                if runtime:
                    from runner.image_runtime import overlaps
                    targets=[runtime['work_dir'],runtime['output_dir'],*runtime['assets'].values(),*[m['target'] for m in runtime.get('mounts',{}).values()]]
                    if any(overlaps(target,'/tmp/mini-eval-artifacts') for target in targets):
                        raise RuntimeError('container directory overlaps independent artifact mount')
                args.extend(['--mount',f'type=bind,source={artifact_root},target=/tmp/mini-eval-artifacts'])
            if task.get('process_logs_v1') and runtime:
                from .process_logs import prepare as prepare_logs
                process_log_config=prepare_logs(self,task,directory/'logs',container=True)
                if process_log_config:args.extend(['--mount','type=bind,source='+str(directory/'logs')+',target='+process_log_config['root']])
            for source, target, readonly in [
                (Path(self.config['assets_root']), '/opt/mini-eval/assets', True),
                (Path(self.config['cache_root']), '/opt/mini-eval/cache', True),
                (directory/'work', '/tmp/mini-eval-work', False),
                (artifact_output, '/tmp/mini-eval-output', False)]:
                args.extend(['--mount', f'type=bind,source={source},target={target}'+(',readonly' if readonly else '')])
            args.extend(['--env', 'MINI_EVAL_WORK_DIR=/tmp/mini-eval-work',
                         '--env', 'MINI_EVAL_ASSETS_DIR=/opt/mini-eval/assets',
                         '--env', 'MINI_EVAL_CACHE_DIR=/opt/mini-eval/cache'])
        if runtime:
            aliases = [(directory/'work', runtime['work_dir'], False), (artifact_output, runtime['output_dir'], False)]
            for name, target in runtime['assets'].items():
                host = self.runtime_snapshot['config']['assets'].get(name)
                if host is None:
                    raise RuntimeError('logical asset is not configured: '+name)
                validate_storage({**self.config, 'assets_root': host, 'cache_root': host}, self.config.get('_supervisor_state', self.path), self.config.get('_config_path'))
                aliases.append((host, target, True))
            from .mounts import resolve as resolve_mounts
            aliases += resolve_mounts(runtime.get('mounts',{}), self.runtime_snapshot['config'], self.config, self.config.get('_supervisor_state',self.path), directory, output_root=artifact_output)
            for source, target, readonly in aliases:
                if target not in (WORK, OUTPUT):
                    args.extend(['--mount', f'type=bind,source={source},target={target}'+(',readonly' if readonly else '')])
            args.extend(['--pull', 'never', '--entrypoint', runtime['interpreter'], '--workdir', ADAPTER, '--env', 'PYTHONPATH='+ADAPTER])
        if task.get('accelerator_type')=='npu':
            from .accelerators import docker_args
            args.extend(docker_args(self.config,task))
        elif task['gpu_uuid']:
            if self.config.get('gpu_runtime') == 'nvidia':
                args.extend(['--runtime', 'nvidia', '--env', 'NVIDIA_VISIBLE_DEVICES=' + task['gpu_uuid'],
                             '--env', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility'])
            else:
                args.extend(['--gpus', 'device=' + task['gpu_uuid']])
        if task.get('accelerator_type','gpu')=='gpu' and task['benchmark'] in ('robotwin-render', 'cs-client'):
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
        if task.get('log_protocol') == 2:
            journal='/tmp/mini-eval-work/.mini-eval-protocol' if task.get('artifact_layout') else '/tmp/mini-eval-output/.mini-eval-protocol'
            command = [command[0], '-m', 'runner.log_capture', journal, *command]
        if task['timeout_seconds'] == 0:
            # No total deadline; Worker lease watchdog and cancellation still own cleanup.
            # Bypass even an old image's supervisor, which interprets zero as immediate expiry.
            args.extend([task['image'], *(command[1:] if runtime else command)])
        else:
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
            config['_episode_timing_v1'] = bool(task.get('episode_timing_v1'))
            config.update(runner_config('/tmp/mini-eval-artifacts' if artifact_root else None))
            if process_log_config:config['_process_logs']=process_log_config
            if is_service(task):
                service=task['client_spec']['service']
                config.update(port=service['container_port'], bind_host='0.0.0.0')
                health_port=service.get('readiness',{}).get('port','business')
                config['health_port']=config['port'] if health_port=='business' else health_port
            (execution_dirs(self.workspace, task['execution_id'])/'input.json').write_text(json.dumps(config))
            with tempfile.TemporaryDirectory(prefix='eval-input-') as directory:
                path = Path(directory)/'input.json'
                path.write_text(json.dumps(config)); path.chmod(0o644)
                docker('cp', str(path), self.name(task['execution_id']) + ':' + INPUT_PATH)

    def upload_artifacts(self, task, deadline=None):
        execution = task['execution_id']
        if task.get('artifact_transport') == 'shared':
            self.register_shared_artifacts(task, retry_transient=True)
            return
        artifacts = HostBackend(self).artifacts(execution) if self.host_mode() else read_artifacts(self.name(execution))
        for name, data in artifacts:
            requested = time.monotonic()
            if self.stop_event.is_set() or (deadline is not None and requested >= deadline[0]):
                raise RuntimeError('worker stopped or lease expired during artifact upload')
            reply = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution})
            if deadline is not None:
                if time.monotonic() >= deadline[0]:
                    raise RuntimeError('lease expired during artifact upload')
                deadline[0] = requested + reply['lease_seconds'] - 2
            if reply.get('cancel'):
                return False  # Main loop observes cancellation and owns cleanup.
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

    def send_protocol_events(self, execution, path):
        offset = self.state.get('protocol_event_offset', 0)
        if not path.exists():return True
        with path.open('rb') as source:
            source.seek(offset)
            for _ in range(16):
                line = source.readline(65537)
                if not line:return True
                if not line.endswith(b'\n'):return False
                event = json.loads(line)
                self.api.post(f'worker/executions/{execution}/episodes', event)
                offset += len(line)
                self.state['protocol_event_offset'] = offset
                self.save()
        return False

    def send_task_result(self, execution):
        template = (self.state or {}).get('task', {}).get('client_spec', {}).get('template', {})
        if template.get('integration_mode') != 'sdk-task-result':
            return True
        path = execution_dirs(self.workspace, execution) / 'work' / 'task-result.json'
        if not path.exists():
            return True
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
            raise RuntimeError('task result file is invalid or exceeds 16 KiB')
        from runner.task_result_contract import validate
        result = validate(json.loads(path.read_text()))
        digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, allow_nan=False,
            separators=(',', ':')).encode()).hexdigest()
        previous = self.state.get('task_result_sha256')
        if previous and previous != digest:
            raise RuntimeError('task result changed after it was reported')
        if previous == digest:
            return True
        self.api.post(f'worker/executions/{execution}/task-result', result)
        self.state['task_result_sha256'] = digest
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
        # Existing execution journals keep their negotiated protocol across upgrades.
        if self.state is not None:
            task = {**task, 'log_protocol': self.state.get('task', {}).get('log_protocol', 1), 'process_logs_v1':self.state.get('task',{}).get('process_logs_v1',False), 'episode_timing_v1':self.state.get('task',{}).get('episode_timing_v1',False)}
        sender = collector = None
        log_gap = False
        log_drain_started = None
        log_collect_started = None
        observed_exit_code = None
        execution = task['execution_id']
        if self.state is None:
            self.state = {'service_started_at': time.time(), 'host_approval': self.config.get('_host_approval', {}), 'runtime': self.runtime_snapshot, 'storage': self.storage_snapshot, 'task': task, 'offset': 0, 'sequence': 0, 'created': False, 'pending': None}
            self.save()
        deadline = [time.monotonic() + task['lease_seconds'] - 2]
        done, lease_lost = threading.Event(), threading.Event()
        startup_done, startup_abort = threading.Event(), threading.Event()
        startup_error = []
        heartbeat_backoff, report_backoff = Backoff(), Backoff()

        def defer(error, backoff):
            if not transient(error):
                raise error
            # Return to the heartbeat loop after waiting; never advance a
            # reliable delivery cursor or pretend this wait renewed the lease.
            if not backoff.wait(error, self.stop_event, deadline[0]):
                raise RuntimeError('worker stopped or lease expired') from error

        def check_startup():
            if startup_error:
                raise startup_error[0]
            if self.stop_event.is_set() or lease_lost.is_set() or time.monotonic() >= deadline[0]:
                raise RuntimeError('worker stopped or lease expired before start')

        def renew_startup():
            interval = min(2, max(.2, (deadline[0] - time.monotonic()) / 4))
            retry = Backoff()
            while not startup_done.wait(interval):
                try:
                    check_startup()
                    requested = time.monotonic()
                    reply = self.api.post('worker/heartbeat', {
                        'slot_id': self.slot_id, 'execution_id': execution, 'started': False})
                    # A late response must not resurrect a locally expired lease.
                    check_startup()
                    if reply['cancel']:
                        raise StartupCancelled()
                    deadline[0] = requested + reply['lease_seconds'] - 2
                    retry.reset()
                except (urllib.error.URLError, TimeoutError):
                    continue
                except APIError as exc:
                    if transient(exc):
                        retry.wait(exc, startup_done, deadline[0])
                        continue
                    startup_error.append(exc)
                    startup_abort.set()
                    return
                except Exception as exc:
                    startup_error.append(exc)
                    startup_abort.set()
                    return

        def watchdog():
            while not done.wait(.5):
                if self.stop_event.is_set() or startup_abort.is_set() or time.monotonic() >= deadline[0]:
                    lease_lost.set()
                    try:
                        self.stop_container(execution)
                    except Exception:
                        LOG.exception('lease watchdog could not stop container')

        thread = threading.Thread(target=watchdog, daemon=True)
        thread.start()
        probe_runner=None
        def remember_failure(body):
            # Preserve the first terminal result even if completion or cleanup
            # fails. A later missing-unit check must not replace its cause.
            self.state.setdefault('terminal_failure', body)
            self.save()
            return self.state['terminal_failure']
        try:
            while True:
                if self.stop_event.is_set() or lease_lost.is_set() or time.monotonic() >= deadline[0]:
                    raise RuntimeError('worker stopped or lease expired before start')
                requested_at = time.monotonic()
                try:
                    initial = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution, 'started': False})
                    break
                except (APIError, urllib.error.URLError, TimeoutError) as exc:
                    # A busy control plane does not renew the original lease.
                    defer(exc, heartbeat_backoff)
            check_startup()
            heartbeat_backoff.reset()
            deadline[0] = requested_at + initial['lease_seconds'] - 2
            if initial['cancel']:
                self.stop_container(execution)
                self.api.post(f'worker/executions/{execution}/complete', {'status': 'CANCELLED'})
                self.remove(execution)
                self.state = None
                self.save()
                return
            if self.state.get('terminal_failure'):
                self.stop_container(execution)
                self.api.post(f'worker/executions/{execution}/complete', self.state['terminal_failure'])
                self.remove(execution)
                self.state = None
                self.save()
                return
            check_startup()
            startup_thread = threading.Thread(target=renew_startup, daemon=True,
                                              name='startup-lease-'+execution)
            startup_thread.start()
            try:
                with self.start_lock:
                    check_startup()
                    container = self.inspect(execution)
                    check_startup()
                    if not container:
                        if self.state['created'] or task['status'] == 'RUNNING':
                            kind = 'host unit' if self.host_mode() else 'Docker container'
                            detail = ''
                            if self.host_mode():
                                source = 'journal-created' if self.state['created'] else 'control-plane-running'
                                detail = (f"; execution_id={execution}; service_instance_id={task.get('service_instance_id') or 'unknown'}"
                                          f"; unit={HostBackend(self).unit(execution)}; source={source}")
                            raise RuntimeError(f'previously created {kind} is missing; refusing duplicate execution'+detail)
                        # Crash before create is safe to fail; never recreate an uncertain execution.
                        self.state['created'] = True
                        self.save()
                        if task.get('benchmark')=='cs-client' and task.get('client_spec',{}).get('service_mode')!='none':
                            from .probe_dependencies import ready as probes_available
                            if probes_available():
                                from .client_connectivity import verify
                                verify(task['client_spec']['endpoint'])
                        check_startup()
                        try:
                            self.create(task)
                        except OSError as exc:
                            # Local preparation errors are terminal startup failures,
                            # not transient control-plane transport failures. Preserve
                            # the cause before cleanup can remove the launch record.
                            if self.host_mode():
                                raise RuntimeError('host preparation failed: '+str(exc)) from exc
                            raise
                        check_startup()
                        container = self.inspect(execution)
                    if container['State']['Status'] == 'created':
                        check_startup()
                        if self.host_mode():
                            try:
                                HostBackend(self).start(task)
                            except OSError as exc:
                                raise RuntimeError('host launch failed: '+str(exc)) from exc
                        else: docker('start', self.name(execution), timeout=10)
                session = None
                if is_service(task) and task['client_spec']['service'].get('readiness'):
                    while True:
                        check_startup()
                        limit = task['client_spec']['service']['readiness']['startup_timeout']
                        if time.time() - self.state['service_started_at'] > limit:
                            raise RuntimeError('service startup health deadline exceeded')
                        try:
                            session = self.api.post(f'worker/executions/{execution}/service-probe-session')
                            check_startup()
                            break
                        except (urllib.error.URLError, TimeoutError):
                            pass
                        except APIError as exc:
                            if not transient(exc):
                                raise
                            defer(exc, report_backoff)
                            continue
                        self.stop_event.wait(.5)
            finally:
                startup_done.set()
                startup_thread.join(timeout=15)
            check_startup()
            if startup_thread.is_alive():
                raise RuntimeError('startup heartbeat did not finish')
            if is_service(task) and task['client_spec']['service'].get('readiness'):
                from .readiness import ProbeRunner
                assert session is not None
                epoch=session['epoch']
                if session.get('ever_ready'):
                    self.state['service_was_ready']=True;self.save()
                probe_runner=ProbeRunner(task['client_spec']['service'],self.config,self.api,execution,epoch,self.workspace/'.probe-locks')
            if task.get('log_protocol') == 2:
                from .log_stream import Sender, DockerCollector
                directory = execution_dirs(self.workspace, execution)
                protocol_root = HostBackend(self).root if self.host_mode() else directory/('work' if task.get('artifact_layout') else 'output')/'.mini-eval-protocol'
                log_root = protocol_root/'logs' if self.host_mode() else directory/'diagnostic-logs'
                if not self.host_mode():collector = DockerCollector(self.name(execution), log_root)
                sender = Sender(Client(self.config), execution, log_root, Path(str(self.path)+'.logs'),
                                stderr_source=None if self.host_mode() else protocol_root/'stderr-tail.json')
            while True:
                if probe_runner:
                    if probe_runner.error:raise RuntimeError(probe_runner.error)
                    if probe_runner.accepted_ready and not self.state.get('service_was_ready'):
                        self.state['service_was_ready']=True;self.save()
                    limit=task['client_spec']['service']['readiness']['startup_timeout']
                    if not self.state.get('service_was_ready') and time.time()-self.state['service_started_at']>limit:
                        raise RuntimeError('service startup health deadline exceeded')
                if self.stop_event.is_set() or lease_lost.is_set() or time.monotonic() >= deadline[0]:
                    raise RuntimeError('worker stopped or lease expired')
                requested_at = time.monotonic()
                try:
                    reply = self.api.post('worker/heartbeat', {'slot_id': self.slot_id, 'execution_id': execution})
                except (APIError, urllib.error.URLError, TimeoutError) as exc:
                    defer(exc, heartbeat_backoff)
                    continue
                check_startup()  # A late response cannot resurrect an expired lease.
                heartbeat_backoff.reset()
                deadline[0] = requested_at + reply['lease_seconds'] - 2
                if reply['cancel']:
                    self.stop_container(execution)
                container = self.inspect(execution)
                if not container:
                    if reply['cancel'] and self.host_mode():
                        # systemd may unload a successfully stopped transient
                        # unit immediately. stop_container already verified exit.
                        container = {'State': {'Running': False, 'ExitCode': None}}
                    else:
                        raise RuntimeError('container disappeared')
                if not container['State']['Running']:
                    observed_exit_code = container['State'].get('ExitCode')
                if sender:
                    final_path = protocol_root/'final.json'
                    output = final_path.read_text() if final_path.exists() else ''
                    err = ''
                elif self.host_mode(): output, err = HostBackend(self).logs()
                else:
                    output = docker('logs', self.name(execution), timeout=5).stdout
                    err = docker('logs', '--tail', '30', self.name(execution), timeout=5).stderr
                try:
                    if task.get('benchmark') == 'cs-client':
                        integration = task.get('client_spec', {}).get('template', {}).get('integration_mode')
                        if integration == 'sdk-task-result':
                            if not self.send_task_result(execution):
                                continue
                        elif sender:
                            if not self.send_protocol_events(execution, protocol_root/'events.jsonl'):continue
                        elif task['client_spec']['template'].get('episode_reporting'):
                            if not self.send_episode_journal(execution):
                                continue
                        elif not self.send_episodes(execution, output):
                            continue
                    logs_done = True if sender else self.send_logs(execution, output + ('\n'+err if not container['State']['Running'] and err else ''))
                except (APIError, urllib.error.URLError, TimeoutError) as exc:
                    defer(exc, report_backoff)
                    continue
                if is_service(task) and not probe_runner and container['State']['Running'] and not reply['cancel']:
                    probe = service_health(task['client_spec']['service'])
                    if (not probe['ready'] and not self.state.get('service_was_ready') and
                        time.time() - self.state.get('service_started_at', time.time()) > task['client_spec']['service']['startup_timeout']):
                        raise RuntimeError('service startup health deadline exceeded')
                    try:
                        self.api.post(f'worker/executions/{execution}/service-health', probe)
                    except (APIError, urllib.error.URLError, TimeoutError) as exc:
                        # A lost health report must not kill a leased service. The
                        # watchdog still stops it if heartbeats cannot renew.
                        defer(exc, report_backoff)
                        continue
                    if probe['ready']:
                        if not self.state.get('service_was_ready'):
                            self.state['service_was_ready'] = True; self.save()
                if not container['State']['Running']:
                    if not logs_done:
                        continue
                    if sender:
                        if collector and collector.thread.is_alive():
                            if log_collect_started is None:log_collect_started = time.monotonic()
                            if time.monotonic()-log_collect_started < 5:
                                time.sleep(min(.5,task['lease_seconds']/4));continue
                        if log_drain_started is None:
                            if collector:collector.close()
                            log_drain_started = time.monotonic()
                        log_gap = not sender.finish(timeout=0)
                        if log_gap and time.monotonic()-log_drain_started < 5:
                            time.sleep(min(.5,task['lease_seconds']/4))
                            continue  # Keep heartbeats alive during the bounded final drain.
                        if log_gap:LOG.warning('execution log final drain timed out; tail may be incomplete')
                        sender.close()
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
                    command_task=(task.get('client_spec') or {}).get('template',{}).get('task_type')=='command'
                    if command_task:result=None
                    status = 'CANCELLED' if reply['cancel'] else ('SUCCEEDED' if not is_service(task) and code == 0 and (result or command_task) else 'FAILED')
                    body = {'status': status, 'exit_code': code, 'metrics': result if status == 'SUCCEEDED' else None,
                            'error': None if status == 'SUCCEEDED' else (err[-900:] or (f'missing protocol final result; runner exited with code {code}' if not command_task and not is_service(task) and code == 0 and not result else f'runner exited with code {code}'))}
                    if sender:body['log_gap'] = log_gap
                    if status == 'FAILED':
                        body = remember_failure(body)
                    try:
                        if task.get('benchmark') == 'cs-client' and not reply['cancel']:
                            if self.upload_artifacts(task, deadline) is False:
                                continue
                        self.api.post(f'worker/executions/{execution}/complete', body)
                    except (APIError, urllib.error.URLError, TimeoutError) as exc:
                        defer(exc, report_backoff)
                        continue
                    self.remove(execution)
                    self.state = None
                    self.save()
                    return
                report_backoff.reset()
                time.sleep(min(2, max(.2, task['lease_seconds']/4)))
        except StartupCancelled:
            self.stop_container(execution)
            try:
                self.api.post(f'worker/executions/{execution}/complete', {'status': 'CANCELLED', **({'log_gap': True} if task.get('log_protocol') == 2 else {})})
            except APIError as exc:
                if exc.status not in (404, 409):
                    raise
                self.api.post(f'worker/executions/{execution}/cleanup')
            self.remove(execution)
            self.state = None
            self.save()
        except APIError as exc:
            if exc.status in (400, 413, 422):
                remember_failure({'status': 'FAILED', 'exit_code': observed_exit_code, 'error': ('execution data rejected: '+str(exc))[:1000], **({'log_gap': True} if task.get('log_protocol') == 2 else {})})
            self.stop_container(execution)
            if exc.status in (404, 409):
                self.cleanup(execution)
            elif exc.status in (400, 413, 422):
                # Replaying an invalid event/result cannot succeed. Release capacity
                # through a failed completion, while retaining the journal on outages.
                try:
                    self.api.post(f'worker/executions/{execution}/complete',
                        self.state['terminal_failure'])
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
            failure = remember_failure({'status': 'FAILED', 'exit_code': observed_exit_code, 'error': str(exc)[:1000], **({'log_gap': True} if task.get('log_protocol') == 2 else {})})
            self.stop_container(execution)
            try:
                self.api.post(f'worker/executions/{execution}/complete', failure)
            except APIError as api_exc:
                if api_exc.status not in (404, 409):
                    raise
                self.api.post(f'worker/executions/{execution}/cleanup')
            self.remove(execution)
            self.state = None
            self.save()
        finally:
            if sender:sender.close()
            if collector:collector.close()
            startup_done.set()
            if probe_runner:probe_runner.close()
            done.set()
            thread.join(timeout=15)

    def child(self, execution):
        identity = str(uuid.UUID(execution))
        directory = Path(str(self.path) + '.runs')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (identity + '.json')
        config = {**self.config, '_supervisor_state': str(self.path), '_host_approval': self.host_environments.record if self.host_environments else self.config.get('_host_approval', {})}
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
        if self.process_logs:
            self.process_logs.close()
        if self.control:
            self.control.stopping = True
        if self.retention:
            self.retention.close()
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
        from .version import PROTOCOL_VERSION, metadata
        hardware = device(self.config)
        from .probe_credentials import available as probe_references
        from .accelerators import capabilities
        hardware['accelerator_backends']=capabilities(self.config)
        registration = self.api.post('worker/register', {'pool_id': self.config.get('pool_id'),
            **hardware, 'protocol_version': PROTOCOL_VERSION, 'software': {**metadata(),'probe_references':probe_references(self.config)}, 'execution_modes': self.config.get('execution_modes', ['docker', 'host']), 'client_images': self.config.get('client_images', [])})
        self.slot_id = registration['slot_id']
        from .control import Control
        self.control = Control(self, docker, telemetry)
        self.control.source = registration.get('resource_source', 'worker')
        self.control.refresh()
        self.slot_lock = open('/tmp/mini-eval-slot-' + self.slot_id + '.lock', 'a')
        fcntl.flock(self.slot_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.recover_journals()
        self.retention = Retention(self, docker)
        self.retention.start()
        from .process_logs import LogService
        self.process_logs=LogService(self);self.process_logs.start()
        had_task = bool(self.children)
        retry = Backoff()
        while True:
            try:
                for execution, child in list(self.children.items()):
                    if self.threads.get(execution) and self.threads[execution].is_alive():
                        continue
                    if child.state:
                        self.launch(child, child.state['task'])
                    else:
                        self.retention.remember(child.storage_snapshot['config']['workspace_root'])
                        self.retention.mark_ready(execution)
                        self.children.pop(execution)
                        self.threads.pop(execution, None)
                        child.path.unlink(missing_ok=True)
                if once and had_task and not self.children:
                    self.process_logs.close()
                    return
                self.control.adopt()
                body = {'slot_id': self.slot_id, **self.control.report(),
                        'software': {**metadata(),'probe_references':probe_references(self.config)}}
                reply = self.api.post('worker/heartbeat', body)
                self.control.source = reply.get('resource_source', 'worker')
                # Never claim while background configuration can change paths.
                # Readiness is checked before scheduling the next control cycle.
                control_ready = self.control.ready()
                if once and had_task:
                    self.control.refresh(reply)
                    time.sleep(2)
                    continue
                if not control_ready:
                    self.control.refresh(reply)
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
                self.control.refresh(reply)
                retry.reset()
            except (APIError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
                LOG.exception('worker iteration failed; retrying')
                if transient(exc):
                    if not retry.wait(exc, self.stop_event):
                        return
                    continue
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    from .probe_dependencies import start
    start()
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
