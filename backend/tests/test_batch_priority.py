from backend.tests.test_batch_controls import setup
from backend.tests.test_execution import env
from backend.app.core import EvaluationBatch, Job, Run
from backend.app.scheduling import transition


def test_owner_can_reorder_pending_batch_and_revision_is_checked(env):
    main, c, auth = env
    batch, _, _ = setup(env)
    url = f"/api/v1/batches/{batch['id']}/priority"
    assert c.patch(url, headers=auth['alice'], json={'priority': 8, 'expected_revision': 0}).status_code == 200
    body = c.patch(url, headers=auth['alice'], json={'priority': 9, 'expected_revision': 0})
    assert body.status_code == 409
    assert c.get('/api/v1/batches/'+batch['id'], headers=auth['alice']).json()['priority'] == 8
    assert c.patch(url, headers=auth['bob'], json={'priority': 1, 'expected_revision': 1}).status_code == 404
    assert c.patch(url, headers=auth['alice'], json={'priority': 10, 'expected_revision': 1}).status_code == 400
    assert c.patch(url, headers=auth['alice'], json={'priority': -1, 'expected_revision': 1}).status_code == 400
    with main.SessionLocal() as s:
        current = s.get(EvaluationBatch, batch['id'])
        assert current.priority == 8 and current.control_revision == 1


def test_priority_cannot_change_after_queue_is_empty(env):
    main, c, auth = env
    batch, _, _ = setup(env)
    with main.SessionLocal() as s:
        for case in batch['cases']:
            transition(s, s.get(Run, case['run_id']), 'FAILED', 'finished')
        s.commit()
    response = c.patch(f"/api/v1/batches/{batch['id']}/priority", headers=auth['alice'],
                       json={'priority': 4, 'expected_revision': 0})
    assert response.status_code == 409
    assert c.get('/api/v1/batches/'+batch['id'], headers=auth['alice']).json()['priority'] == 0
