import copy
from datetime import timedelta
import pytest
from sqlalchemy import select, func
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import template, batch_body
from backend.app.core import EvaluationBatch, Job, Run, Event, Slot, Worker, Pool, now
from backend.app.scheduling import tick


def setup(env):
    main, c, auth = env
    t = template(env)
    _, slots = node(env)
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0]); slot.protocol_version = 3; slot.client_images = ['approved:cs']
        s.commit()
    return batch_body(t), slots[0]


def check(env, body):
    _, c, auth = env
    r = c.post('/api/v1/batches/preflight', headers=auth['alice'], json=body)
    assert r.status_code == 200, r.text
    return r.json()


def counts(main):
    with main.SessionLocal() as s:
        return [s.scalar(select(func.count()).select_from(cls)) for cls in (EvaluationBatch, Job, Run, Event)]


def test_read_only_normalization_and_revalidation(env):
    main, c, auth = env
    body, sid = setup(env)
    suite = c.post('/api/v1/suites', headers=auth['alice'], json=dict(name='suite', version='1', template_id=body['client']['template_id'], cases=body['cases'])).json()
    body.update(suite_id=suite['id'], cases=[])
    before = counts(main)
    for _ in range(2):
        result = check(env, body)
        assert result['status'] == 'ready' and result['nodes'][0]['ready']
        assert result['task_count'] == len(suite['cases']) and result['cases'] == suite['cases']
    assert counts(main) == before
    assert body['cases'] == []
    assert c.post('/api/v1/batches/preflight', headers=auth['bob'], json=body).status_code == 404
    with main.SessionLocal() as s:
        s.get(Slot, sid).client_images = []; s.commit()
    headers = {**auth['alice'], 'Idempotency-Key':'preflight-test'}
    r = c.post('/api/v1/batches', headers=headers, json=body)
    assert r.status_code == 201
    assert c.post('/api/v1/batches', headers=headers, json=body).json()['id'] == r.json()['id']
    assert tick(main.SessionLocal) == 0
    assert check(env, body)['status'] == 'needs_environment'


@pytest.mark.parametrize('change,code', [
    ({'client_images':[]}, 'client_image'),
    ({'last_seen_at':None}, 'offline'),
    ({'quarantined':True}, 'quarantined'),
    ({'membership_revision':10,'desired_pool_id':'missing'}, 'membership_pending'),
    ({'disk_min_free_mb':1000,'disk_free_mb':1,'disk_at':now()}, 'disk_capacity'),
    ({'disk_min_free_mb':1000,'disk_free_mb':2000,'disk_at':now()-timedelta(seconds=30)}, 'disk_capacity'),
    ({'protocol_version':4}, 'storage_pending'),
    ({'protocol_version':5}, 'runtime_pending'),
])
def test_environment_reasons_match_admission(env, change, code):
    main, c, auth = env
    body, sid = setup(env)
    with main.SessionLocal() as s:
        slot=s.get(Slot,sid)
        for k,v in change.items(): setattr(slot,k,v)
        s.commit()
    result=check(env,body)
    assert code in [r['code'] for r in result['nodes'][0]['reasons']]
    assert result['can_submit'] and not result['nodes'][0]['ready']
    assert c.post('/api/v1/batches',headers=auth['alice'],json=body).status_code==201
    assert tick(main.SessionLocal)==0


@pytest.mark.parametrize('field,value,code', [('max_concurrent',0,'node_concurrency'), ('cpu_capacity',0,'node_cpu'), ('ram_gb_capacity',0,'node_ram')])
def test_node_limits(env,field,value,code):
    main,c,auth=env;body,sid=setup(env)
    with main.SessionLocal() as s:
        setattr(s.get(Worker,s.get(Slot,sid).worker_id),field,value);s.commit()
    assert code in [r['code'] for r in check(env,body)['nodes'][0]['reasons']]
    c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert tick(main.SessionLocal)==0


def test_busy_global_user_endpoint_and_cleanup(env,monkeypatch):
    main,c,auth=env;body,sid=setup(env)
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','1');monkeypatch.setenv('MAX_ACTIVE_PER_USER','1')
    from backend.app.core import ClientTemplate
    with main.SessionLocal() as s:
        s.get(ClientTemplate,body['client']['template_id']).endpoint_exclusive=True;s.commit()
    c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert tick(main.SessionLocal)==1
    result=check(env,body)
    assert {r['code'] for r in result['reasons']}=={'global_concurrency','user_concurrency','endpoint_busy'}
    assert 'gpu_busy' in [r['code'] for r in result['nodes'][0]['reasons']]
    assert result['status']=='waiting_resources'
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.status=='RESERVED'));run.status='FAILED';run.cleanup_confirmed=False;s.commit()
    assert check(env,body)['status']=='waiting_resources'


@pytest.mark.parametrize('change,overrides,code', [
    ({}, {'required_gpu_model':'other'}, 'gpu_model'),
    ({}, {'requested_vram_gb':40}, 'vram_capacity'),
    ({'free_vram_mb':100}, {}, 'vram_busy'),
    ({'telemetry_at':None}, {}, 'telemetry_stale'),
    ({'gpu_utilization':100}, {'gpu_mode':'shared'}, 'gpu_utilization'),
])
def test_gpu_reasons(env,change,overrides,code):
    main,c,auth=env;body,sid=setup(env)
    with main.SessionLocal() as s:
        pool=s.get(Pool,'mock');pool.gpu_model='*';pool.allow_shared=True
        slot=s.get(Slot,sid);slot.gpu_model='test';slot.vram_mb=24576;slot.free_vram_mb=24576;slot.gpu_utilization=0;slot.telemetry_at=now()
        for k,v in change.items():setattr(slot,k,v)
        s.commit()
    body.update(requested_vram_gb=4);body.update(overrides)
    result=check(env,body)
    assert code in [r['code'] for r in result['nodes'][0]['reasons']]
    c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert tick(main.SessionLocal)==0


def test_private_assets_errors_and_no_writes(env, monkeypatch):
    from backend.tests.test_image_runtime import runtime_setup
    main,c,auth=env
    t,token,sid,route,report=runtime_setup(env)
    report['assets']['robotwin']={'ready':False,'error':'secret /host/path'}
    c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':sid,'runtime':report})
    body=batch_body(t);before=counts(main)
    result=check(env,body)
    assert 'robotwin' in str(result) and '/host/path' not in str(result) and '/data/assets' not in str(result)
    for who in ['bob']:
        assert c.post('/api/v1/batches/preflight',headers=auth[who],json=body).status_code==400
    invalid=copy.deepcopy(body);invalid['client']['environment']={'BAD':'x'}
    for path in ['batches/preflight','batches']:
        assert c.post('/api/v1/'+path,headers=auth['alice'],json=invalid).status_code==400
    monkeypatch.setenv('MAX_BATCH_BACKLOG_PER_USER','0')
    assert c.post('/api/v1/batches/preflight',headers=auth['alice'],json=body).status_code==429
    assert counts(main)==before
