"""Release control through existing optimistic-concurrency APIs, never direct DB writes."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from scripts.fleet_release import TARGETS, atomic_json


class API:
    def __init__(self, base, token):
        u = urlsplit(base)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            raise ValueError('invalid platform URL')
        self.base = base.rstrip('/') + '/api/v1/'
        self.token = token

    def __call__(self, path, body=None, method=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token}
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as result:
                return json.load(result)
        except urllib.error.HTTPError as error:
            # Error bodies may contain submitted values; do not echo them.
            raise RuntimeError('platform request rejected with HTTP ' + str(error.code)) from None
        except (OSError, ValueError):
            raise RuntimeError('platform request unavailable; reconcile before repeating mutations') from None


def node_map(api, plan):
    nodes = api('admin/nodes')
    result = {}
    for target in plan['targets']:
        if TARGETS.get(target['address']) != target['node_name']:
            raise ValueError('unapproved target mapping')
        matches = [n for n in nodes if n['node_name'] == target['node_name'] and not n.get('revoked_at')]
        if len(matches) != 1: raise RuntimeError('missing or ambiguous platform node')
        result[target['node_name']] = matches[0]
    return result


def drain(api, plan, journal, timeout=300, clock=time.monotonic, sleep=time.sleep):
    if journal.state is None: raise RuntimeError('release lock required')
    nodes = node_map(api, plan)
    for name, node in nodes.items():
        step = 'drain:' + name
        inputs = {'node_id': node['id']}
        if not journal.begin(step, inputs): continue
        record = journal.state['steps'][step]
        record.update(node_id=node['id'], was_enabled=node['enabled'], original_revision=node['management_revision'])
        atomic_json(journal.path, journal.state)
        if node['enabled']:
            changed = api('admin/nodes/' + node['id'], {'enabled': False, 'expected_revision': node['management_revision']}, 'PATCH')
            record['owned_revision'] = changed['management_revision']
            atomic_json(journal.path, journal.state)
        journal.finish(step)
    until = clock() + timeout
    while True:
        current = node_map(api, plan)
        if any(n['enabled'] for n in current.values()):
            raise RuntimeError('admission changed during drain; stop release')
        if all(n['held_executions'] == 0 for n in current.values()): return
        if clock() >= until:
            raise TimeoutError('drain timed out; running tasks were not terminated; restore owned admission changes')
        sleep(min(2, max(0, until-clock())))


def restore_admission(api, plan, journal):
    if journal.state is None: raise RuntimeError('release lock required')
    nodes = node_map(api, plan)
    conflicts = []
    for name, node in nodes.items():
        record = journal.state['steps'].get('drain:' + name)
        if not record or record.get('restored') or not record.get('was_enabled'): continue
        if node['id'] != record.get('node_id') or record.get('owned_revision') is None:
            conflicts.append(name); continue
        if node['management_revision'] != record['owned_revision'] or node['enabled']:
            conflicts.append(name); continue
        api('admin/nodes/' + node['id'], {'enabled': True, 'expected_revision': record['owned_revision']}, 'PATCH')
        record['restored'] = True
        atomic_json(journal.path, journal.state)
    if conflicts: raise RuntimeError('admission ownership changed; manual reconciliation required: ' + ', '.join(conflicts))


def assert_release_ready(api, plan, release_id):
    for node in node_map(api, plan).values():
        current = api('admin/nodes/' + node['id'] + '/upgrades')
        if not current['slots'] or any(not s['online'] or s['protocol_version'] != plan['target_protocol'] or
                (s.get('software') or {}).get('version') != plan['target_worker'] or
                (s.get('software') or {}).get('build_digest') != release_id for s in current['slots']):
            raise RuntimeError('worker release is not ready: ' + node['node_name'])
        agent = (node.get('connection') or {}).get('agent') or {}
        if agent.get('status') != 'online' or agent.get('version') != plan['target_agent'] or agent.get('digest') != release_id or agent.get('config_status') != 'applied':
            raise RuntimeError('agent release is not ready: ' + node['node_name'])
