from datetime import timedelta
from sqlalchemy import select, func
from backend.app.core import Slot, Pool, Run, MembershipAudit, now
from backend.app.scheduling import tick
from backend.tests.test_execution import env, node, submit, claim


def move(env, ids, pool, revisions=None, role='admin'):
    _, c, auth = env
    return c.put('/api/v1/admin/resource-memberships', headers=auth[role],
        json={'pool_id': pool, 'members': [{'slot_id': x, 'expected_revision': (revisions or {}).get(x, 0)} for x in ids]})


def test_poolless_registration_identity_and_reconnect(env):
    main, c, auth = env
    token, ids = node(env)
    assert c.get('/api/v1/worker/self', headers=token).json()['node_name'] == 'node'
    assert c.get('/api/v1/worker/self', headers=auth['admin']).status_code == 401
    body = dict(gpu_index=1, gpu_uuid='mock:node:1', gpu_model='virtual', vram_gb=0)
    r = c.post('/api/v1/worker/register', headers=token, json=body)
    assert r.status_code == 200, r.text
    slot = r.json()['slot_id']
    assert r.json()['membership_state'] == 'unassigned'
    assert move(env, [slot], 'mock', {slot: 1}).status_code == 200
    assert move(env, [slot], None, {slot: 2}).status_code == 200
    for old in ('mock', 'no-longer-exists', None):
        r = c.post('/api/v1/worker/register', headers=token, json={**body, 'pool_id': old})
        assert r.status_code == 200, r.text
        assert r.json()['pool_id'] is None
        assert r.json()['membership_revision'] == 3
    assert c.post('/api/v1/worker/register', headers=token, json={**body, 'gpu_uuid': 'mock:other'}).status_code == 409
    assert c.post('/api/v1/worker/register', headers=token, json={**body, 'gpu_model': 'physical'}).status_code == 400
    other, _ = node(env, 'other')
    assert c.post('/api/v1/worker/register', headers=other, json=body).status_code == 409
    assert c.post(f'/api/v1/worker/slots/{slot}/claim', headers=token).json()['task'] is None


def test_atomic_permissions_versions_compatibility(env):
    main, c, auth = env
    _, ids = node(env, count=2)
    assert move(env, ids, None, role='alice').status_code == 403
    assert move(env, [ids[0], ids[0]], None).status_code == 400
    assert move(env, ids, 'missing').status_code == 409
    with main.SessionLocal() as s:
        s.add(Pool(id='physical', name='physical', gpu_model='*'))
        s.add(Pool(id='large', name='large', gpu_model='virtual', min_vram_gb=1))
        s.commit()
    assert move(env, ids, 'physical').status_code == 409
    assert move(env, ids, 'large').status_code == 409
    assert move(env, ids, None, {ids[1]: 1}).status_code == 409
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(MembershipAudit)) == 0
        assert all(s.get(Slot, x).membership_revision == 0 for x in ids)
    r = move(env, ids, None)
    assert r.status_code == 200
    assert all(x['membership_state'] == 'unassigned' for x in r.json())
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(MembershipAudit)) == 2


def test_drain_reservations_recovery_cleanup_and_summary(env):
    main, c, auth = env
    token, ids = node(env, count=2)
    with main.SessionLocal() as s:
        s.add(Pool(id='target', name='target', gpu_model='virtual'))
        s.commit()
    submit(env)
    task = claim(env, token, ids[0])
    submit(env, 'bob')
    assert tick(main.SessionLocal) == 1
    r = move(env, ids, 'target')
    assert r.status_code == 200, r.text
    assert [x['membership_state'] for x in r.json()] == ['draining', 'assigned']
    assert claim(env, token, ids[0])['execution_id'] == task['execution_id']
    with main.SessionLocal() as s:
        pending = s.scalar(select(Run).where(Run.status == 'PENDING'))
        assert pending.slot_id is None and pending.reserved_cpu == 0
        old = s.get(Slot, ids[0])
        old.last_seen_at = now()-timedelta(seconds=60)
        s.commit()
    pools = {p['id']: p for p in c.get('/api/v1/pools', headers=auth['alice']).json()}
    assert pools['mock']['member_count'] == 1
    assert pools['target']['member_count'] == pools['target']['incoming_count'] == 1
    # Offline and terminal-but-unclean cannot transfer ownership.
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.execution_id == task['execution_id']))
        run.lease_expires_at = now()-timedelta(seconds=60)
        s.commit()
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        assert s.get(Slot, ids[0]).pool_id == 'mock'
        s.get(Pool, 'target').enabled = False
        s.commit()
    c.post(f"/api/v1/worker/executions/{task['execution_id']}/cleanup", headers=token)
    rows = c.get('/api/v1/workers', headers=auth['admin']).json()
    assert next(x for x in rows if x['id'] == ids[0])['membership_state'] == 'blocked'
    r = move(env, [ids[0]], None, {ids[0]: 1})
    assert r.json()[0]['membership_state'] == 'unassigned'


def test_machine_budgets_across_pools_and_storage_gate(env, monkeypatch):
    from backend.app.core import Worker
    from backend.tests.test_storage import CONFIG
    main, c, auth = env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '10')
    monkeypatch.setenv('MAX_ACTIVE_PER_USER', '10')
    token, ids = node(env, count=2)
    with main.SessionLocal() as s:
        w = s.get(Worker, s.get(Slot, ids[0]).worker_id)
        w.cpu_capacity = w.ram_gb_capacity = 1
        wid = w.id
        s.add(Pool(id='other', name='other', gpu_model='virtual', cpu_per_job=1, ram_gb_per_job=1))
        s.get(Pool, 'mock').cpu_per_job = 1
        s.get(Pool, 'mock').ram_gb_per_job = 1
        s.commit()
    assert move(env, [ids[1]], 'other').status_code == 200
    submit(env)
    r = c.post('/api/v1/jobs', headers=auth['bob'], json=dict(title='other', benchmark='mock', model_ref='m', episodes=1, pool_id='other'))
    assert r.status_code == 201
    assert tick(main.SessionLocal) == 1
    assert tick(main.SessionLocal) == 0
    assert c.put(f'/api/v1/admin/nodes/{wid}/storage', headers=auth['admin'], json={'expected_revision':0,'config':CONFIG}).status_code == 200
    assert move(env, [ids[0]], 'other').status_code == 200
    # Membership is applied, but storage still blocks both cards.
    assert tick(main.SessionLocal) == 0
    rows = c.get('/api/v1/workers', headers=auth['admin']).json()
    assert all('storage_pending' in x['admission_reasons'] for x in rows)
