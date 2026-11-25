"""The wizard policy is frozen into both client and managed-service claims."""
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select

from backend.app.core import EvaluationOrchestration, Job, Run, Slot, now
from backend.app.scheduling import runtime_mismatch, tick
from backend.tests.test_cs_protocol import batch_body, template
from backend.tests.test_execution import env, node
from backend.tests.test_orchestrations import setup as managed_setup, submit as managed_submit


@pytest.mark.parametrize('bad', [0, -1, True, 1.5, '60', 2**63])
def test_invalid_finite_tolerance_is_rejected_by_submission(env, bad):
    _, client, auth = env
    body = batch_body(template(env))
    body['disconnect_tolerance_seconds'] = bad
    response = client.post('/api/v1/batches', headers=auth['alice'], json=body)
    assert response.status_code == 400


def test_finite_policy_reaches_original_claim_and_rerun_draft(env, monkeypatch):
    main, client, auth = env
    identifier = template(env)
    token, slots = node(env)
    body = batch_body(identifier)
    body['disconnect_tolerance_seconds'] = 90
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED', '1')
    with main.SessionLocal() as session:
        slot = session.get(Slot, slots[0])
        slot.protocol_version = 18
        slot.client_images = ['approved:cs']
        slot.software = {'capabilities':['disconnected_execution_v1']}
        slot.storage_report = {'revision':0, 'status':'applied'}
        slot.storage_reported_at = now()
        slot.runtime_report = {'revision':0, 'applied_revision':0,
                               'images':{'approved:cs':{'ready':True}}, 'assets':{}}
        slot.runtime_reported_at = now()
        session.commit()
    response = client.post('/api/v1/batches', headers=auth['alice'], json=body)
    assert response.status_code == 201, response.text
    batch = response.json()
    with main.SessionLocal() as session:
        jobs = list(session.scalars(select(Job).where(Job.batch_id == batch['id'])))
        assert {j.client_spec['disconnect_tolerance_seconds'] for j in jobs} == {90}
    draft = client.get(f"/api/v1/batches/{batch['id']}/rerun-draft", headers=auth['alice'])
    assert draft.status_code == 200, draft.text
    assert draft.json()['submission']['disconnect_tolerance_seconds'] == 90
    scheduled = tick(main.SessionLocal)
    preview = client.post('/api/v1/batches/preflight', headers=auth['alice'], json=body).json()
    assert scheduled >= 1, [(row.get('slot_id'), row.get('reasons')) for row in preview.get('nodes', [])]
    claim = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=token,
        json={'known_execution_ids':[], 'request_id':str(uuid.uuid4()), 'recovery_version':1})
    assert claim.status_code == 200, claim.text
    receipt = claim.json()['recovery_claim']
    assert receipt['policy'] == {'version':1, 'disconnect_tolerance_seconds':90}
    with main.SessionLocal() as session:
        assert session.get(Run, claim.json()['task']['run_id']).status == 'STARTING'


def test_finite_policy_does_not_silently_fall_back_to_legacy_worker(monkeypatch):
    spec = {'template':{'execution_mode':'docker'}, 'disconnect_tolerance_seconds':90}
    job = SimpleNamespace(client_spec=spec, benchmark='cs-client')
    slot = SimpleNamespace(protocol_version=18, software={}, execution_modes=['docker'])
    assert '有限断联容忍' in runtime_mismatch(slot, job)
    slot.software = {'capabilities':['disconnected_execution_v1']}
    assert '有限断联容忍' in runtime_mismatch(slot, job)
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED', '1')
    assert runtime_mismatch(slot, job) is None


def test_managed_service_and_clients_freeze_same_policy(env, monkeypatch):
    main, _, _ = env
    *_, body = managed_setup(env, monkeypatch)
    body['disconnect_tolerance_seconds'] = 120
    monkeypatch.setenv('RECOVERY_EXECUTION_ENABLED', '1')
    with main.SessionLocal() as session:
        for slot in session.scalars(select(Slot)):
            slot.software = {'capabilities':['disconnected_execution_v1']}
        session.commit()
    batch = managed_submit(env, body, 'finite-disconnect')
    with main.SessionLocal() as session:
        jobs = list(session.scalars(select(Job).where(Job.batch_id == batch['id'])))
        assert jobs and {j.client_spec['disconnect_tolerance_seconds'] for j in jobs} == {120}
        definition = session.get(EvaluationOrchestration, batch['id']).definition['services'][0]
        assert definition['disconnect_tolerance_seconds'] == 120
        from backend.app.orchestration_scheduler import server_job
        assert server_job(definition, jobs[0].owner_id).client_spec['disconnect_tolerance_seconds'] == 120
    tick(main.SessionLocal)
    with main.SessionLocal() as session:
        services = list(session.scalars(select(Job).where(Job.service_instance_id.is_not(None))))
        assert services and {j.client_spec['disconnect_tolerance_seconds'] for j in services} == {120}
