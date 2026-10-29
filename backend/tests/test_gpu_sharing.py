from datetime import timedelta
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.app.core import Job, Pool, Run, Slot, now
from backend.app.scheduling import tick, transition


def setup_gpu(env, count=2, protocol=2):
    main, c, auth = env
    pool = c.post('/api/v1/admin/pools', headers=auth['admin'], json={
        'name': 'mixed', 'allow_shared': True, 'max_tasks_per_gpu': 2}).json()['id']
    slots, tokens = [], []
    for i in range(count):
        token = c.post('/api/v1/admin/worker-credentials', headers=auth['admin'], json={
            'node_name': f'gpu{i}', 'max_concurrent': 4}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        response = c.post('/api/v1/worker/register', headers=headers, json={
            'pool_id': pool, 'gpu_index': 0, 'gpu_uuid': f'GPU-{i}',
            'gpu_model': ['RTX8000', 'RTX4090'][i], 'vram_gb': [48, 24][i], 'protocol_version': protocol})
        assert response.status_code == 200, response.text
        slot = response.json()['slot_id']
        c.post('/api/v1/worker/heartbeat', headers=headers, json={'slot_id': slot,
            'telemetry': {'free_vram_mb': [48, 24][i]*1024, 'gpu_utilization': 10}})
        slots.append(slot)
        tokens.append(headers)
    return pool, slots, tokens


def submit_gpu(env, pool, owner='alice', **overrides):
    _, c, auth = env
    r = c.post('/api/v1/jobs', headers=auth[owner], json={
        'title': 'gpu', 'benchmark': 'mock', 'model_ref': 'm', 'episodes': 1, 'pool_id': pool,
        'gpu_mode': 'shared', 'requested_vram_gb': 8, **overrides})
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_heterogeneous_registration_model_filter_and_validation(env):
    main, c, auth = env
    pool, slots, tokens = setup_gpu(env)
    j = submit_gpu(env, pool, required_gpu_model='RTX4090')
    assert tick(main.SessionLocal) == 1
    with main.SessionLocal() as s:
        assert s.scalar(select(Run).where(Run.job_id == j)).slot_id == slots[1]
        assert s.get(Pool, pool).gpu_model == '*'
        assert {s.get(Slot, x).gpu_model for x in slots} == {'RTX8000', 'RTX4090'}
    invalid = {'title': 'x', 'benchmark': 'mock', 'model_ref': 'm', 'episodes': 1, 'pool_id': pool, 'gpu_mode': 'shared'}
    assert c.post('/api/v1/jobs', headers=auth['alice'], json=invalid).status_code == 400
    assert c.patch(f'/api/v1/admin/pools/{pool}', headers=auth['alice'], json={}).status_code == 403
    assert c.post('/api/v1/worker/register', headers=tokens[0], json={
        'pool_id': pool, 'gpu_index': 1, 'gpu_uuid': 'mock:bad:1', 'gpu_model': 'virtual', 'vram_gb': 0}).status_code == 400


def test_shared_budget_claim_cancel_and_snapshot(env, monkeypatch):
    main, c, auth = env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL', '10')
    monkeypatch.setenv('MAX_ACTIVE_PER_USER', '10')
    pool, slots, tokens = setup_gpu(env, 1)
    jobs = [submit_gpu(env, pool, requested_vram_gb=20) for _ in range(3)]
    assert tick(main.SessionLocal) == 2
    tasks = []
    for _ in range(2):
        task = c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0],
            json={'known_execution_ids': [t['execution_id'] for t in tasks]}).json()['task']
        tasks.append(task)
    assert len({t['execution_id'] for t in tasks}) == 2
    assert tick(main.SessionLocal) == 0
    for task in tasks:
        c.post('/api/v1/worker/heartbeat', headers=tokens[0], json={'slot_id': slots[0], 'execution_id': task['execution_id'], 'started': False})
    with main.SessionLocal() as s:
        assert all(s.scalar(select(Run).where(Run.execution_id == t['execution_id'])).status == 'STARTING' for t in tasks)
        p = s.get(Pool, pool)
        p.cpu_per_job = 4
        p.max_tasks_per_gpu = 3
        s.commit()
    assert c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0]).json()['task']['cpus'] == 1
    c.post(f'/api/v1/jobs/{tasks[0]["job_id"]}/cancel', headers=auth['alice'])
    replies = [c.post('/api/v1/worker/heartbeat', headers=tokens[0], json={
        'slot_id': slots[0], 'execution_id': t['execution_id']}).json() for t in tasks]
    assert [r['cancel'] for r in replies] == [True, False]
    with main.SessionLocal() as s:
        held = s.scalars(select(Run).where(Run.slot_id == slots[0])).all()
        assert sum(r.reserved_vram_mb for r in held) == 40*1024
        assert len({r.allocation_index for r in held}) == 2
    assert tick(main.SessionLocal) == 0  # 60 GiB would exceed 46 GiB budget.


def test_exclusive_blocks_shares_and_old_worker_cannot_share(env):
    main, c, _ = env
    pool, slots, _ = setup_gpu(env, 1, protocol=1)
    shared = submit_gpu(env, pool)
    assert tick(main.SessionLocal) == 0
    exclusive = submit_gpu(env, pool, gpu_mode='exclusive', requested_vram_gb=0)
    assert tick(main.SessionLocal) == 1
    with main.SessionLocal() as s:
        assert s.get(Job, exclusive).status == 'RESERVED'
        assert s.get(Job, shared).status == 'PENDING'
        s.get(Slot, slots[0]).protocol_version = 2
        s.commit()
    assert tick(main.SessionLocal) == 0


def test_telemetry_stale_high_usage_and_low_memory(env):
    main, c, _ = env
    pool, slots, tokens = setup_gpu(env, 1)
    submit_gpu(env, pool)
    for free, util in [(1024, 0), (48*1024, 95)]:
        c.post('/api/v1/worker/heartbeat', headers=tokens[0], json={
            'slot_id': slots[0], 'telemetry': {'free_vram_mb': free, 'gpu_utilization': util}})
        assert tick(main.SessionLocal) == 0
    c.post('/api/v1/worker/heartbeat', headers=tokens[0], json={
        'slot_id': slots[0], 'telemetry_failed': True})
    assert tick(main.SessionLocal) == 0
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0])
        slot.free_vram_mb, slot.gpu_utilization = 48*1024, 0
        slot.telemetry_at = now()-timedelta(seconds=16)
        s.commit()
    assert tick(main.SessionLocal) == 0


def test_low_utilization_preferred_and_starting_memory_charged(env, monkeypatch):
    main, c, _ = env
    pool, slots, tokens = setup_gpu(env)
    c.post('/api/v1/worker/heartbeat', headers=tokens[1], json={
        'slot_id': slots[1], 'telemetry': {'free_vram_mb': 12*1024, 'gpu_utilization': 1}})
    jobs = [submit_gpu(env, pool) for _ in range(2)]
    assert tick(main.SessionLocal) == 2
    with main.SessionLocal() as s:
        first = s.scalar(select(Run).where(Run.job_id == jobs[0]))
        second = s.scalar(select(Run).where(Run.job_id == jobs[1]))
        assert first.slot_id == slots[1]
        assert second.slot_id == slots[0]  # Free telemetry still 12 GiB; first pending 8 GiB must count.


def test_quarantine_cleanup_and_pool_move(env, monkeypatch):
    main, c, auth = env
    pool, slots, tokens = setup_gpu(env, 1)
    j = submit_gpu(env, pool)
    tick(main.SessionLocal)
    task = c.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0]).json()['task']
    new_pool = c.post('/api/v1/admin/pools', headers=auth['admin'], json={'name': 'other'}).json()['id']
    body = {'pool_id': new_pool, 'gpu_index': 0, 'gpu_uuid': 'GPU-0', 'gpu_model': 'RTX8000', 'vram_gb': 48, 'protocol_version': 2}
    assert c.post('/api/v1/worker/register', headers=tokens[0], json=body).status_code == 409
    with main.SessionLocal() as s:
        run = s.scalar(select(Run).where(Run.job_id == j))
        run.lease_expires_at = now()-timedelta(seconds=120)
        s.commit()
    tick(main.SessionLocal)
    submit_gpu(env, pool, owner='bob')
    assert tick(main.SessionLocal) == 0
    assert c.post('/api/v1/worker/register', headers=tokens[0], json=body).status_code == 409
    c.post(f'/api/v1/worker/executions/{task["execution_id"]}/cleanup', headers=tokens[0])
    assert c.post('/api/v1/worker/register', headers=tokens[0], json=body).status_code == 200
