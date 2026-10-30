"""Durable storage intent, independent of credentials and execution journals."""
import json
import os
from pathlib import Path
from .workspace import storage_paths, validate_storage, directory_capacity, probe_storage


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Storage:
    def __init__(self, config, state_path):
        self.path = Path(str(state_path) + '.storage.json')
        self.state_path = state_path
        self.config_path = config.get('_config_path')
        self.runner_image = config.get('runner_image', 'mini-eval-runner:dev')
        if self.path.exists():
            self.record = json.loads(self.path.read_text())
        else:
            initial = storage_paths(config)
            self.record = {'bootstrap': initial, 'effective': initial, 'applied_revision': 0, 'validated': False}
            atomic_json(self.path, self.record)
        self.revision = self.record['applied_revision']
        self.status = 'applied' if self.record['validated'] else 'waiting'
        self.error = None
        self.attempted = None

    def snapshot(self):
        return {'config': dict(self.record['effective']), 'revision': self.record['applied_revision']}

    def report(self):
        capacity = {}
        try:
            validate_storage(self.record['effective'], self.state_path, self.config_path)
            capacity = directory_capacity(self.record['effective'])
        except (ValueError, OSError) as exc:
            self.status = 'failed'
            self.error = str(exc)[:1000]
        return {'revision': self.revision, 'applied_revision': self.record['applied_revision'],
                'status': self.status, 'effective': self.record['effective'],
                'capacity': capacity, 'error': self.error}

    def update(self, desired, slot_id, busy, docker):
        revision = desired['revision']
        if revision == self.record['applied_revision'] and self.record['validated']:
            self.revision = revision
            # Recheck host permissions/capacity every heartbeat, with no repeated container probe.
            try:
                validate_storage(self.record['effective'], self.state_path, self.config_path)
                self.status, self.error = 'applied', None
            except (ValueError, OSError) as exc:
                self.status, self.error = 'failed', str(exc)[:1000]
            return
        self.revision = revision
        if busy:
            self.status, self.error = 'waiting', None
            return
        if self.attempted == revision:
            return
        self.attempted = revision
        try:
            candidate = dict(desired['config'] or self.record['bootstrap'])
            if desired['config']:
                candidate['workspace_root'] = str(Path(candidate['workspace_root'])/'slots'/slot_id)
            candidate = validate_storage(candidate, self.state_path, self.config_path, create=True)
            probe_storage(candidate, self.runner_image, docker)
            record = {**self.record, 'effective': candidate, 'applied_revision': revision, 'validated': True}
            atomic_json(self.path, record)
            self.record = record
            self.status, self.error = 'applied', None
        except Exception as exc:
            self.status, self.error = 'failed', str(exc)[:1000]
