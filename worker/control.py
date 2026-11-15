"""Single-flight background resource/configuration work for a slot supervisor.

Only immutable snapshots cross into the heartbeat loop. A hung filesystem call
consumes one daemon thread, never the heartbeat thread or an unbounded queue.
"""
import copy
import threading
import time


class Control:
    def __init__(self, worker, docker, telemetry):
        self.worker, self.docker, self.telemetry = worker, docker, telemetry
        self.thread = None
        self.result = None
        self.sampled = 0
        self.started = 0
        self.source = 'worker'
        self.stopping = False

    def report(self):
        result = self.result
        if result is None or time.monotonic() - self.sampled >= 10:
            return {'disk': {'free_mb': 0, 'min_free_mb': 1}, 'telemetry_failed': True}
        return copy.deepcopy(result['body'])

    def ready(self):
        return (self.result is not None and time.monotonic() - self.sampled < 10
                and not (self.thread and self.thread.is_alive()))

    def refresh(self, reply=None):
        if self.stopping or (self.thread and self.thread.is_alive()): return
        w = self.worker
        reply = copy.deepcopy(reply or {})
        busy = bool(w.children) or reply.get('storage_busy', True)
        source = self.source
        self.started = time.monotonic()
        def collect():
            try:
                if 'storage' in reply:
                    w.storage.update(reply['storage'], w.slot_id, busy, self.docker)
                storage = w.storage.snapshot()
                config = {**w.config, **storage['config']}
                if 'host' in reply:
                    w.host_environments.update(reply['host'], busy)
                if 'runtime' in reply:
                    w.runtime.update(reply['runtime'], busy, config, self.docker)
                body = {'storage': w.storage.report(), 'runtime': w.runtime.report(),
                        'host': w.host_environments.report()}
                from .workspace import disk_status
                body['disk'] = disk_status(storage['config']['workspace_root'], config)
                if source != 'agent':
                    try: body['telemetry'] = self.telemetry(config)
                    except Exception: body['telemetry_failed'] = True
                result = {'body': body, 'storage': storage, 'runtime': w.runtime.snapshot()}
                # A slow cycle is not a fresh sample, even if it eventually returns.
                if time.monotonic() - self.started < 10 and not self.stopping:
                    self.result = result
                    self.sampled = self.started
            except Exception:
                self.result = None
        self.thread = threading.Thread(target=collect, daemon=True, name='worker-control')
        self.thread.start()

    def adopt(self):
        if not self.ready(): return False
        w, result = self.worker, self.result
        w.storage_snapshot = copy.deepcopy(result['storage'])
        w.runtime_snapshot = copy.deepcopy(result['runtime'])
        w.config.update(w.storage_snapshot['config'])
        w.config['client_images'] = w.runtime_snapshot['config']['images']
        from pathlib import Path
        w.workspace = Path(w.config['workspace_root'])
        return True
