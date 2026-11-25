"""The priority manifest must cover every FastAPI method, including HEAD."""
import csv
import json
from pathlib import Path
import re

from fastapi.routing import APIRoute
import pytest

from backend.app.priority_routes import (accepts_role, category_for, compile_manifest,
                                         legacy_category_for, manifest, role_for)


def test_every_registered_route_has_one_explicit_classification(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'priority-route-test-only')
    from backend.app.main import app

    registered = {(method, route.path) for route in app.routes if isinstance(route, APIRoute)
                  for method in route.methods}
    declared = {(method, path) for method, path, _, _, _ in manifest()}
    assert declared == registered
    for method, path in sorted(registered):
        concrete = re.sub(r'\{[^}]+\}', 'sample', path)
        category = category_for(concrete, method)
        assert category != 'fallback', (method, path)
        assert role_for(concrete, method) is None or role_for(concrete, method) in {
            'execution-control', 'interaction', 'worker-data', 'bulk-transfer'}


def test_legacy_categories_match_prechange_route_audit():
    audit = Path(__file__).resolve().parents[2] / 'docs/validation/priority-api-isolation-20261124/registered-routes.tsv'
    with audit.open() as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    extra = audit.parents[1] / 'disconnected-execution-20261124/recovery-route-contracts.json'
    rows.extend(json.loads(extra.read_text())['legacy_routes'])
    assert len(rows) == len(manifest())
    for row in rows:
        concrete = re.sub(r'\{[^}]+\}', 'sample', row['path'])
        assert legacy_category_for(concrete, row['method']) == row['current_category']


def test_request_contract_audit_covers_current_routes_and_declared_limits(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'priority-route-test-only')
    from backend.app.main import app
    from backend.tests.priority_route_audit import collect
    audit = Path(__file__).resolve().parents[2] / 'docs/validation/priority-api-isolation-20261124/request-contracts.json'
    actual = collect(app)
    expected = json.loads(audit.read_text())
    extra = json.loads((audit.parents[1] / 'disconnected-execution-20261124/recovery-route-contracts.json').read_text())
    expected['routes'].extend(extra['routes'])
    overrides = {(row['method'], row['path']):row for row in extra.get('route_overrides', [])}
    assert set(overrides) <= {(row['method'], row['path']) for row in expected['routes']}
    expected['routes'] = [overrides.get((row['method'], row['path']), row) for row in expected['routes']]
    expected['body_schemas'].update(extra['body_schemas'])
    # Historical isolation evidence remains immutable; additions are audited
    # separately and every route/schema is still compared exhaustively.
    key = lambda row: (row['method'], row['path'])
    expected['routes'] = sorted(expected['routes'], key=key)
    actual['routes'] = sorted(actual['routes'], key=key)
    assert expected == actual
    assert actual['gateway_body_byte_limit'] is None
    assert len(actual['routes']) == len(manifest())
    assert all(row['confirmation'] for row in actual['routes'])


@pytest.mark.parametrize('method,path,category,role', [
    ('POST', '/api/v1/worker/heartbeat', 'execution_control', 'execution-control'),
    ('POST', '/api/v1/worker/executions/e/complete', 'execution_control', 'execution-control'),
    ('POST', '/api/v1/worker/executions/e/task-result', 'worker_result', 'worker-data'),
    ('POST', '/api/v1/worker/slots/s/claim', 'worker_claim', 'worker-data'),
    ('POST', '/api/v1/agent/heartbeat', 'worker_observation', 'worker-data'),
    ('POST', '/api/v1/worker/executions/e/logs-v2', 'bulk_transfer', 'bulk-transfer'),
    ('GET', '/api/v1/jobs/j/logs', 'page_read', 'interaction'),
    ('GET', '/api/v1/batches/b/monitor', 'page_read', 'interaction'),
    ('POST', '/api/v1/batches', 'user_action', 'interaction'),
    ('POST', '/api/v1/batches/preflight', 'user_long', 'interaction'),
    ('POST', '/api/v1/admin/ssh-onboarding/check', 'user_long', 'interaction'),
    ('PATCH', '/api/v1/batches/b/priority', 'user_action', 'interaction'),
    ('PUT', '/api/v1/admin/nodes/n/runtime', 'user_action', 'interaction'),
    ('DELETE', '/api/v1/batches/b', 'user_action', 'interaction'),
    ('HEAD', '/api/v1/jobs/j/attempts/r/shared-artifacts/media/m', 'bulk_transfer', 'bulk-transfer'),
    ('GET', '/api/v1/jobs/j/attempts/r/artifacts/a/media', 'bulk_transfer', 'bulk-transfer'),
    ('GET', '/healthz', 'health', None),
])
def test_selected_routes(method, path, category, role):
    assert category_for(path, method) == category
    assert role_for(path, method) == role


@pytest.mark.parametrize('method,path', [
    ('GET', '/api/v1/worker/heartbeat'),
    ('POST', '/api/v1/worker/heartbeat/extra'),
    ('POST', '/api/v1/worker/executions/e/complete/extra'),
    ('POST', '/api/v1/admin/unreviewed-action'),
    ('POST', '/api/v1/worker/executions/e/unknown'),
])
def test_unknown_routes_cannot_claim_priority(method, path):
    assert category_for(path, method) == 'fallback'
    assert role_for(path, method) == 'interaction'
    assert legacy_category_for(path, method) == 'ordinary'


@pytest.mark.parametrize('items', [
    [{'method': 'POST', 'path': '/x', 'category': 'execution_control', 'legacy_category': 'control', 'extra': True}],
    [{'method': 'TRACE', 'path': '/x', 'category': 'execution_control', 'legacy_category': 'control'}],
    [{'method': 'POST', 'path': 'x', 'category': 'execution_control', 'legacy_category': 'control'}],
    [{'method': 'POST', 'path': '/x', 'category': 'fallback', 'legacy_category': 'ordinary'}],
    [{'method': 'POST', 'path': '/x', 'category': 'user_action', 'legacy_category': 'unknown'}],
    [{'method': 'POST', 'path': '/x', 'category': 'execution_control', 'legacy_category': 'control'}] * 2,
])
def test_malformed_manifest_fails_closed(items):
    with pytest.raises(ValueError):
        compile_manifest(items)


def test_overlapping_routes_with_different_categories_fail_closed(monkeypatch):
    from backend.app import priority_routes
    entries = [
        {'method': 'GET', 'path': '/api/v1/audit-conflict/{id}',
         'category': 'page_read', 'legacy_category': 'ordinary'},
        {'method': 'GET', 'path': '/api/v1/audit-conflict/fixed',
         'category': 'execution_control', 'legacy_category': 'control'},
    ]
    monkeypatch.setattr(priority_routes, 'manifest', lambda: compile_manifest(entries))
    with pytest.raises(ValueError, match='ambiguous priority route'):
        priority_routes.category_for('/api/v1/audit-conflict/fixed', 'GET')


def test_wrong_role_cannot_accept_control_or_bulk_request():
    assert accepts_role('execution-control', '/api/v1/worker/heartbeat', 'POST')
    assert not accepts_role('interaction', '/api/v1/worker/heartbeat', 'POST')
    assert not accepts_role('execution-control', '/api/v1/worker/executions/e/logs-v2', 'POST')
    assert accepts_role('bulk-transfer', '/api/v1/worker/executions/e/logs-v2', 'POST')
    assert not accepts_role('execution-control', '/api/v1/new-route', 'POST')
    assert accepts_role('interaction', '/api/v1/new-route', 'POST')
    assert accepts_role('bulk-transfer', '/healthz', 'GET')
