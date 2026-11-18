import pytest
from backend.tests.test_execution import env
from backend.tests.test_monitoring import batch, get
from backend.app.core import Slot, Run, now


@pytest.mark.parametrize('kind', ['gpu', 'npu'])
def test_client_placement_latest_attempt_and_deleted_slot(env, kind):
    main, client, auth = env
    b, sid = batch(env)
    assert get(env, b)['cases'][0]['placement'] is None
    with main.SessionLocal() as s:
        slot = s.get(Slot, sid)
        slot.accelerator_type = kind
        slot.gpu_index = 3
        slot.gpu_model = 'test-model'
        run = s.get(Run, b['cases'][0]['run_id'])
        run.slot_id = sid
        s.commit()
    case = get(env, b)['cases'][0]
    assert case['placement']['accelerator_type'] == kind
    assert case['placement']['gpu_index'] == 3
    assert case['placement']['gpu_model'] == 'test-model'
    with main.SessionLocal() as s:
        s.get(Slot, sid).deleted_at = now()
        s.commit()
    assert get(env, b)['cases'][0]['placement']['id'] == sid
    with main.SessionLocal() as s:
        s.add(Run(job_id=b['cases'][0]['job_id'], attempt=2))
        s.commit()
    assert get(env, b)['cases'][0]['placement'] is None


def test_service_uses_assigned_run_not_configured_target(env):
    from backend.tests.test_services import setup
    from backend.app.core import Job, ServiceInstance
    from backend.app.services import view
    main, client, auth = env
    _, sid, body = setup(env)
    response = client.post('/api/v1/admin/services', headers=auth['admin'], json=body)
    assert response.status_code == 201, response.text
    assert response.json()['placement'] is None
    with main.SessionLocal() as s:
        item = s.get(ServiceInstance, response.json()['id'])
        job = s.get(Job, item.current_job_id)
        job.runs[-1].slot_id = sid
        slot = s.get(Slot, sid); slot.accelerator_type = 'npu'; slot.gpu_index = 7
        s.flush()
        assert view(s, item)['placement']['gpu_index'] == 7
        assert view(s, item)['placement']['accelerator_type'] == 'npu'
        job.runs[-1].slot_id = None
        assert view(s, item)['placement'] is None
        item.current_job_id = None
        assert view(s, item)['placement'] is None
