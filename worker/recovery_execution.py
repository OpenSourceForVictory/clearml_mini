"""Negotiated execution lifecycle; control never waits for business data delivery."""
import json
import logging
import math
import base64
from pathlib import Path
import time

from .host_backend import HostBackend
from .recovery_delivery import RecoveryDelivery
from .recovery_results import execution_outbox
from .services import is_service, health
from .storage import atomic_json
from .workspace import execution_dirs

LOG = logging.getLogger('mini-eval-worker')


def protocol_root(worker):
    task = worker.state['task']
    if worker.host_mode():
        return HostBackend(worker).root
    directory = execution_dirs(worker.workspace, task['execution_id'])
    return directory / ('work' if task.get('artifact_layout') else 'output') / '.mini-eval-protocol'


def output(worker):
    from .client import docker
    task = worker.state['task']
    if task.get('log_protocol') == 2:
        path = protocol_root(worker) / 'final.json'
        return (path.read_text() if path.exists() else ''), ''
    if worker.host_mode():
        return HostBackend(worker).logs()
    result = docker('logs', worker.name(task['execution_id']), timeout=5)
    return result.stdout, result.stderr


def preserve_output(worker, stdout, stderr):
    """Save before Docker removal; a full result queue must not lose its source."""
    record = {'execution_id':worker.state['task']['execution_id'],
              'proof':worker.state['recovery'], 'stdout':stdout, 'stderr':stderr}
    atomic_json(Path(str(worker.path)+'.recovery-output'), record)


def upload_optional_artifacts(worker, task):
    """Attempt bounded best-effort artifacts without gating process cleanup.

    Reliable protocol results use the separate outbox. Historical uploaded
    artifacts remain optional data and can be lost when their API is refused.
    """
    if task.get('benchmark') != 'cs-client':
        return
    try:
        if task.get('artifact_transport') == 'shared':
            worker.register_shared_artifacts(task, retry_transient=True)
            return
        if worker.host_mode():
            files = HostBackend(worker).artifacts(task['execution_id'])
        else:
            from .artifacts import read_artifacts
            files = read_artifacts(worker.name(task['execution_id']))
        for name, data in files:
            worker.api.post(f"worker/executions/{task['execution_id']}/artifacts",
                            {'name': name, 'content_base64': base64.b64encode(data).decode()})
    except Exception as exc:
        LOG.warning('optional recovery artifacts unavailable; cleanup continues (%s)',
                    type(exc).__name__)


def resume_collection(worker, outbox):
    if worker.state.get('recovery_results_collected'):
        return True
    path = Path(str(worker.path)+'.recovery-output')
    record = json.loads(path.read_text())
    if (record['execution_id'] != worker.state['task']['execution_id']
            or record['proof'] != worker.state['recovery']):
        raise ValueError('saved output does not belong to original execution')
    return worker.collect_recovery_results(outbox, output=record['stdout'],
                                          protocol_root=protocol_root(worker), final=True)


def completion(task, snapshot, stdout, stderr, failure=None):
    result = None
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)['final']
            candidate = {k:value[k] for k in ('success_rate', 'episode_count', 'runtime')}
            if (type(candidate['episode_count']) is not int
                    or not 1 <= candidate['episode_count'] <= 10000
                    or candidate['episode_count'] != task.get('episodes', candidate['episode_count'])
                    or any(type(candidate[k]) not in (int, float) or not math.isfinite(candidate[k])
                           for k in ('success_rate', 'runtime'))
                    or not 0 <= candidate['success_rate'] <= 1 or candidate['runtime'] < 0):
                continue
            result = candidate
            break
        except (ValueError, KeyError, TypeError, OverflowError):
            continue
    command = task.get('client_spec', {}).get('template', {}).get('task_type') == 'command'
    if command:
        result = None
    code = snapshot['exit_code']
    stopped = snapshot['control']['directive'] == 'stop' or snapshot['stop_reason']
    succeeded = not failure and not stopped and not is_service(task) and code == 0 and (command or result is not None)
    return {'status':'SUCCEEDED' if succeeded else 'FAILED', 'exit_code':code,
            'metrics':result if succeeded else None, 'pending_results':True,
            'error':None if succeeded else (failure or stderr[-900:] or
                (f'missing protocol final result; runner exited with code {code}'
                 if code == 0 and not command and not is_service(task) and result is None
                 else f'runner exited with code {code}'))}


class ServiceProbe:
    """A rejected/expired probe epoch is retried, never interpreted as a crash."""
    def __init__(self, worker, started_at):
        self.worker, self.started_at, self.runner = worker, started_at, None

    def step(self):
        worker = self.worker
        task = worker.state['task']
        if not is_service(task):
            return
        service = task['client_spec']['service']
        execution = task['execution_id']
        sample = None
        if service.get('readiness'):
            if self.runner and self.runner.error:
                self.close()
            if self.runner is None:
                from .readiness import ProbeRunner
                session = worker.api.post(f'worker/executions/{execution}/service-probe-session')
                if session.get('ever_ready'):
                    worker.state['service_was_ready'] = True
                    worker.save()
                self.runner = ProbeRunner(service, worker.config, worker.api, execution,
                                          session['epoch'], worker.workspace/'.probe-locks')
            sample = self.runner.last
            limit = service['readiness']['startup_timeout']
        else:
            sample = health(service)
            limit = service['startup_timeout']
        # Actual local readiness, not successful delivery, governs this local
        # startup failure. Platform readiness still needs its own fresh ACK.
        if sample and sample['ready'] and not worker.state.get('service_was_ready'):
            worker.state['service_was_ready'] = True
            worker.save()
        if (sample and not sample['ready'] and not worker.state.get('service_was_ready')
                and time.monotonic()-self.started_at > limit):
            worker.state['recovery_local_failure'] = 'service startup health deadline exceeded'
            worker.save()
        if not service.get('readiness'):
            worker.api.post(f'worker/executions/{execution}/service-health', sample)

    def close(self):
        if self.runner:
            self.runner.close()
            self.runner = None


def execute(worker):
    """Called only by Worker.execute for a persisted negotiated assignment."""
    from .client import Client
    from .log_stream import Sender, DockerCollector
    from .recovery_finish import resume_results

    launch = worker.start_recovery()
    if launch is None and worker.state is None:
        return  # Platform acknowledged a never-started assignment.
    monitor = worker.monitor_recovery(launch, control=True)
    probe = ServiceProbe(worker, launch['started_at'])
    sender = collector = None
    closed = False
    try:
        task = worker.state['task']
        execution = task['execution_id']
        outbox = execution_outbox(worker)
        delivery = RecoveryDelivery(worker.api, outbox, apply_results=True,
                                    execution_id=execution)
        if task.get('log_protocol') == 2:
            root = protocol_root(worker)
            logs = root/'logs' if worker.host_mode() else execution_dirs(worker.workspace, execution)/'diagnostic-logs'
            if not worker.host_mode():
                collector = DockerCollector(worker.name(execution), logs)
            sender = Sender(Client(worker.config), execution, logs, Path(str(worker.path)+'.logs'),
                            stderr_source=None if worker.host_mode() else root/'stderr-tail.json')
        while not worker.stop_event.is_set():
            snapshot = monitor.snapshot()
            if snapshot['blocked_reason'] or snapshot['state'] == 'missing':
                # Keep the control owner alive to report missing, without
                # inventing a successful exit or launching a replacement.
                worker.stop_event.wait(.5)
                continue
            exited = snapshot['state'] == 'exited'
            if worker.state.get('recovery_local_failure') and not exited:
                monitor.runtime.stop(snapshot['identity'])
            if not exited:
                try:
                    probe.step()
                except Exception as exc:
                    LOG.warning('recovery service observation deferred (%s)', type(exc).__name__)
            stdout, stderr = output(worker)
            if exited:
                preserve_output(worker, stdout, stderr)
            try:
                worker.collect_recovery_results(outbox, output=stdout,
                                                protocol_root=protocol_root(worker), final=exited)
            except Exception as exc:
                LOG.warning('recovery result collection deferred; source retained (%s)', type(exc).__name__)
            # Finalization is intentionally ahead of result/log network I/O.
            # Acknowledgement of a RUNNING sample cannot release this runtime.
            ack = snapshot.get('acknowledged', {})
            if exited and ack.get('state') == 'exited' and ack.get('exit_code') == snapshot['exit_code']:
                probe.close()
                if sender is None:
                    try:
                        worker.send_logs(execution, stdout + ('\n'+stderr if stderr else ''), max_batches=1)
                    except Exception as exc:
                        LOG.warning('optional recovery logs unavailable; cleanup continues (%s)',
                                    type(exc).__name__)
                upload_optional_artifacts(worker, task)
                if collector:
                    collector.close()
                log_gap = False
                if sender:
                    # The control ACK may arrive before the diagnostic sender
                    # catches up after an outage. Give the final tail the same
                    # bounded drain as ordinary executions, then mark a gap.
                    log_gap = not sender.finish(timeout=5)
                    sender.close()
                monitor.close()
                closed = True
                body = completion(task, snapshot, stdout, stderr, worker.state.get('recovery_local_failure'))
                if sender:
                    body['log_gap'] = log_gap
                worker.finalize_recovery(body)
                resume_results(worker)
                return
            try:
                delivery.step(limit=1)
                if sender is None:
                    worker.send_logs(execution, stdout + ('\n'+stderr if exited and stderr else ''), max_batches=1)
            except Exception as exc:
                LOG.warning('recovery data delivery deferred (%s)', type(exc).__name__)
            worker.stop_event.wait(.5)
    finally:
        # Worker shutdown relinquishes supervision, not execution ownership.
        # Journals and original monotonic deadlines survive a process restart.
        try:
            probe.close()
            if collector:
                collector.close()
            if sender:
                sender.close()
        finally:
            if not closed:
                monitor.close()
