"""Durable registration of acceptance resources; only registered IDs may be controlled."""
import json
from pathlib import Path
import re
from scripts.fleet_release import atomic_json


class Registry:
    def __init__(self, path, run_id):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}', run_id): raise ValueError('invalid acceptance ID')
        self.path = Path(path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'run_id': run_id, 'resources': {}, 'evidence': []}
        if self.state['run_id'] != run_id: raise ValueError('acceptance identity mismatch')

    def register(self, kind, identity, owner, name):
        if kind not in ('batch', 'client-template', 'server-template'): raise ValueError('unsupported test resource')
        if not name.startswith(self.state['run_id'] + '-'): raise ValueError('resource is outside test namespace')
        key = kind + ':' + identity
        value = {'kind': kind, 'id': identity, 'owner': owner, 'name': name}
        old = self.state['resources'].get(key)
        if old and old != value: raise ValueError('resource ownership changed')
        self.state['resources'][key] = value
        atomic_json(self.path, self.state)

    def resource(self, kind, identity, owner):
        value = self.state['resources'].get(kind + ':' + identity)
        if not value or value['owner'] != owner: raise ValueError('unregistered test resource or owner mismatch')
        return value

    def record(self, scenario, status, details):
        if status not in ('passed', 'failed', 'skipped', 'unverified'): raise ValueError('invalid evidence status')
        self.state['evidence'].append({'scenario': scenario, 'status': status, 'details': details})
        atomic_json(self.path, self.state)

    def cancel_batch(self, api, identity, owner):
        registered = self.resource('batch', identity, owner)
        current = api('batches/' + identity + '/monitor')
        batch = current.get('batch', current)
        if batch.get('title') != registered['name']: raise ValueError('test batch identity changed')
        return api('batches/' + identity + '/control',
                   {'action': 'cancel', 'expected_revision': batch['control_revision']}, 'POST')

    def disable_template(self, api, kind, identity, owner):
        registered = self.resource(kind + '-template', identity, owner)
        templates = api('admin/server-templates' if kind == 'server' else 'client-templates')
        current = next((x for x in templates if x['id'] == identity), None)
        if not current or current['name'] != registered['name']: raise ValueError('test template identity changed')
        if current['enabled']:
            api('admin/' + kind + '-templates/' + identity, {'enabled': False}, 'PATCH')
