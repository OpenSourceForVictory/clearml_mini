"""The split gateway and server admission must agree on control routes."""
import asyncio
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from backend.app.db_budget import Admission, RouteGroup, Settings, request_category


GATEWAY = Path(__file__).resolve().parents[2] / 'deploy' / 'split-api-nginx.conf'


def gateway_control_path(path):
    config = GATEWAY.read_text()
    exact = re.findall(r'location = (/api/v1/[^ ]+) \{ proxy_pass http://\$control_backend\$request_uri;', config)
    patterns = re.findall(r'location ~ (\^/api/v1/[^\s{]+)\s*\{[^}]*?proxy_pass http://\$control_backend\$request_uri;',
                          config, re.DOTALL)
    return path in exact or any(re.fullmatch(pattern, path) for pattern in patterns)


@pytest.mark.parametrize('path', [
    '/api/v1/worker/heartbeat', '/api/v1/agent/stop-permit',
    '/api/v1/worker/executions/abc/service-health',
    '/api/v1/worker/executions/abc/service-probe-session',
    '/api/v1/worker/executions/abc/complete',
    '/api/v1/worker/executions/abc/cleanup',
    '/api/v1/jobs/abc/cancel', '/api/v1/batches/abc/control',
    '/api/v1/jobs/abc/retry', '/api/v1/batches/abc/retry-failed',
    '/api/v1/admin/services/abc/control',
    '/api/v1/orchestrations/abc/services/def/control',
    '/api/v1/agent/heartbeat', '/api/v1/worker/executions/abc/logs',
    '/api/v1/worker/executions/abc/episodes',
    '/api/v1/worker/executions/abc/task-result',
    '/api/v1/worker/slots/abc/claim', '/api/v1/admin/health',
    '/api/v1/jobs/abc', '/api/v1/worker/heartbeat/extra',
    '/api/v1/worker/executions/abc/complete/extra',
])
def test_gateway_matches_server_control_classification(path):
    assert gateway_control_path(path) is (request_category(path, 'POST') == 'control')


def test_every_registered_api_route_reaches_its_classified_process(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'split-routing-test-only')
    from backend.app.main import app

    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = re.sub(r'\{[^}]+\}', 'sample', route.path)
        for method in route.methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            expected = request_category(path, method) == 'control'
            assert gateway_control_path(path) is expected, (method, route.path)
            checked += 1
    assert checked > 100


@pytest.mark.parametrize('group,path,method,accepted', [
    ('control', '/api/v1/worker/heartbeat', 'POST', True),
    ('control', '/api/v1/worker/heartbeat', 'GET', False),
    ('control', '/api/v1/agent/heartbeat', 'POST', False),
    ('control', '/api/v1/worker/executions/e/logs', 'POST', False),
    ('ordinary', '/api/v1/worker/heartbeat', 'POST', False),
    ('ordinary', '/api/v1/worker/executions/e/complete', 'POST', False),
    ('ordinary', '/api/v1/agent/heartbeat', 'POST', True),
    ('ordinary', '/api/v1/worker/executions/e/logs', 'POST', True),
    ('ordinary', '/api/v1/jobs/e', 'GET', True),
    ('control', '/healthz', 'GET', True),
    ('ordinary', '/healthz', 'GET', True),
])
def test_route_group_rejects_mismatch_before_handler(group, path, method, accepted):
    calls, messages = [], []

    async def app(scope, receive, send):
        calls.append(scope['path'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'OK'})

    async def send(message):
        messages.append(message)

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    admission = Admission(app, Settings(), route_group=group)
    asyncio.run(admission({'type': 'http', 'path': path, 'method': method}, receive, send))
    assert bool(calls) is accepted
    assert messages[0]['status'] == (200 if accepted else 404)


def test_invalid_route_group_rejected():
    with pytest.raises(ValueError, match='API route group'):
        Admission(None, Settings(), route_group='all')
    with pytest.raises(ValueError, match='API route group'):
        RouteGroup(None, route_group='all')


@pytest.mark.parametrize('group,path,accepted', [
    ('control', '/api/v1/worker/heartbeat', True),
    ('control', '/api/v1/agent/heartbeat', False),
    ('ordinary', '/api/v1/worker/heartbeat', False),
    ('ordinary', '/api/v1/agent/heartbeat', True),
    ('ordinary', '/api/v1/worker/executions/e/logs-v2', True),
    ('control', '/healthz', True),
    ('ordinary', '/healthz', True),
])
def test_route_group_without_admission_does_not_limit_requests(group, path, accepted):
    called, messages = [], []

    async def app(scope, receive, send):
        called.append(scope['path'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'OK'})

    async def send(message):
        messages.append(message)

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    router = RouteGroup(app, group)
    for _ in range(3):
        asyncio.run(router({'type': 'http', 'path': path, 'method': 'GET' if path == '/healthz' else 'POST'},
                           receive, send))
    assert len(called) == (3 if accepted else 0)
    assert [message['status'] for message in messages if message['type'] == 'http.response.start'] == [
        200 if accepted else 404] * 3
