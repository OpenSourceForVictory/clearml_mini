"""Durable per-slot runtime settings; probes never delay lease heartbeats."""
import copy
import json
import threading
import time
from pathlib import Path
from .storage import atomic_json
from .workspace import validate_storage, probe_storage


class Runtime:
    def __init__(self, config, state_path):
        self.path = Path(str(state_path)+'.runtime.json')
        self.state_path = state_path
        initial = {'images': list(config.get('client_images', [])), 'assets': dict(config.get('asset_mappings', {}))}
        if self.path.exists():
            self.record = json.loads(self.path.read_text())
        else:
            self.record = {'bootstrap': initial, 'effective': initial, 'applied_revision': 0}
            atomic_json(self.path, self.record)
        self.revision = self.record['applied_revision']
        self.resources = {'images': {}, 'assets': {}}
        self.probed_at = 0
        self.thread = None
        self.signature = None
        self.stopping = threading.Event()

    def snapshot(self):
        return {'revision': self.record['applied_revision'], 'config': copy.deepcopy(self.record['effective'])}

    def report(self):
        resources = self.resources if time.monotonic()-self.probed_at < 30 else {'images': {}, 'assets': {}}
        return {'revision': self.revision, 'applied_revision': self.record['applied_revision'],
                'effective': self.record['effective'], **resources}

    def update(self, desired, busy, config, docker):
        self.revision = desired['revision']
        if self.revision != self.record['applied_revision'] and not busy:
            self.record = {**self.record, 'effective': copy.deepcopy(desired['config'] if desired['config'] is not None else self.record['bootstrap']),
                           'applied_revision': self.revision}
            atomic_json(self.path, self.record)
            self.resources = {'images': {}, 'assets': {}}
            self.probed_at = 0
        snapshot = self.snapshot()
        signature = json.dumps([snapshot, {k: config.get(k) for k in ('workspace_root','assets_root','cache_root')}], sort_keys=True)
        if signature != self.signature:
            self.resources = {'images': {}, 'assets': {}}
            self.probed_at = 0
            self.signature = signature
        if self.thread and self.thread.is_alive():
            return
        if time.monotonic()-self.probed_at < 10:
            return
        def probe():
            results = {'images': {}, 'assets': {}}
            for image in snapshot['config']['images']:
                if self.stopping.is_set(): return
                try:
                    docker('image', 'inspect', image, timeout=5)
                    results['images'][image] = {'ready': True, 'error': None}
                except Exception as exc:
                    results['images'][image] = {'ready': False, 'error': str(exc)[:1000]}
            for name, host in snapshot['config']['assets'].items():
                if self.stopping.is_set(): return
                try:
                    values = validate_storage({**config, 'assets_root': host, 'cache_root': host}, self.state_path, config.get('_config_path'))
                    probe_storage(values, config.get('runner_image', 'mini-eval-runner:dev'), docker)
                    results['assets'][name] = {'ready': True, 'error': None}
                except Exception as exc:
                    results['assets'][name] = {'ready': False, 'error': str(exc)[:1000]}
            if signature == self.signature:
                self.resources = results
                self.probed_at = time.monotonic()
        self.thread = threading.Thread(target=probe, daemon=True, name='runtime-probe')
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=35)
