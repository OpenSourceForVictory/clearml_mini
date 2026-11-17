"""Frequent status reports update only the latest state without the scheduler mutex."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.tests.test_execution import env, node
from backend.tests.test_service_readiness import deploy
from backend.app import scheduling, execution, node_agents, services


def test_worker_and_agent_idle_heartbeats_do_not_take_global_lock(env, monkeypatch):
    _, client, _ = env
    token, slots = node(env)
    registration = client.post('/api/v1/agent/register', headers=token,
                               json={'boot_id': uuid.uuid4().hex, 'version': '1'})
    assert registration.status_code == 200
    session = registration.json()['session']
    def forbidden(*_args, **_kwargs):
        raise AssertionError('global scheduling lock used for idle telemetry')
    monkeypatch.setattr(scheduling, 'lock', forbidden)
    monkeypatch.setattr(execution, 'lock', forbidden)
    monkeypatch.setattr(node_agents, 'lock', forbidden)
    assert client.post('/api/v1/worker/heartbeat', headers=token, json={'slot_id': slots[0]}).status_code == 200
    body = {'session': session, 'sequence': 1, 'applied_revision': 0, 'config_status': 'applied'}
    assert client.post('/api/v1/agent/heartbeat', headers=token, json=body).status_code == 200
    assert client.post('/api/v1/agent/heartbeat', headers=token, json=body).status_code == 200


def test_service_health_updates_latest_without_global_lock(env, monkeypatch):
    _, client, _ = env
    token, _, _, task, _ = deploy(env)
    path = '/api/v1/worker/executions/' + task['execution_id']
    epoch = client.post(path+'/service-probe-session', headers=token).json()['epoch']
    def forbidden(*_args, **_kwargs):
        raise AssertionError('global scheduling lock used for probe result')
    monkeypatch.setattr(services, 'lock', forbidden)
    assert client.post(path+'/service-health', headers=token,
                       json={'ready': True, 'epoch': epoch, 'sequence': 1}).status_code == 200


def test_fast_lock_rejects_contention_without_wait(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduling, 'lock', lambda _s, wait=True: calls.append(wait) or False)
    with pytest.raises(HTTPException) as error:
        scheduling.fast_lock(SimpleNamespace())
    assert error.value.status_code == 503
    assert error.value.headers['Retry-After'] == '1'
    assert calls == [False]
