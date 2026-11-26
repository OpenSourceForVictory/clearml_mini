import copy
from datetime import timedelta
import pytest
from sqlalchemy import select, func, event
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


def test_many_cases_reuse_selected_template_query(env):
    main, client, auth = env
    body, _ = setup(env)
    engine = main.SessionLocal.kw['bind']
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, 'before_cursor_execute', record)
    try:
        counts = []
        for size in (1, 20):
            body['cases'] = [dict(case_id=str(index), task_name=f'case-{index}', episodes=1, seed=index)
                             for index in range(size)]
            statements.clear()
            response = client.post('/api/v1/batches/preflight', headers=auth['alice'], json=body)
            assert response.status_code == 200, response.text
            assert response.json()['task_count'] == size
            counts.append(len(statements))
        assert counts[1] == counts[0]
    finally:
        event.remove(engine, 'before_cursor_execute', record)


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


def test_client_runtime_reason_names_image_and_missing_execution_mode(env):
    main,_,_=env
    body,sid=setup(env)
    with main.SessionLocal() as s:
        s.get(Slot,sid).execution_modes=['host']
        s.commit()
    result=check(env,body)
    messages=[r['message'] for r in result['nodes'][0]['reasons'] if r['code']=='client_runtime']
    assert len(messages)==1
    assert '客户端容器镜像「approved:cs」' in messages[0]
    assert '未启用容器执行' in messages[0]


def test_client_runtime_version_reason_names_host_environment():
    from types import SimpleNamespace as N
    from backend.app.scheduling import runtime_mismatch
    slot=N(protocol_version=12)
    job=N(client_spec={'template':{'execution_mode':'host','host_runtime':{'environment_name':'robotwin','working_directory':'business'}}})
    assert runtime_mismatch(slot,job)=='客户端宿主机环境「robotwin」的业务执行目录需要 Worker 协议 13（当前 12）'


def test_preflight_distinguishes_unapproved_missing_and_failed_images(env):
    main,_,_=env;body,sid=setup(env)
    with main.SessionLocal() as s:
        slot=s.get(Slot,sid);worker=s.get(Worker,slot.worker_id)
        worker.runtime_config={'images':[],'assets':{},'directories':{}}
        slot.client_images=[]
        s.commit()
    assert '未在该节点的容器环境中批准' in check(env,body)['nodes'][0]['reasons'][0]['message']
    with main.SessionLocal() as s:
        slot=s.get(Slot,sid);worker=s.get(Worker,slot.worker_id)
        worker.runtime_config={'images':['approved:cs'],'assets':{},'directories':{}}
        slot.runtime_report={'effective':{'images':['approved:cs']},'images':{'approved:cs':{'ready':False,'error':'Error: No such image: approved:cs'}}}
        s.commit()
    reasons=check(env,body)['nodes'][0]['reasons']
    assert len([r for r in reasons if r['code']=='client_image'])==1
    assert '节点上没有该镜像' in reasons[0]['message']
    with main.SessionLocal() as s:
        slot=s.get(Slot,sid)
        slot.runtime_report={'effective':{'images':['approved:cs']},'images':{'approved:cs':{'ready':False,'error':'private /srv/secret'}}}
        s.commit()
    text=str(check(env,body))
    assert '节点镜像检查失败' in text and '/srv/secret' not in text


def test_preflight_names_pending_runtime_and_host_environment():
    from types import SimpleNamespace as N
    from backend.app.preflight import slot_reason,blocker_reason
    job=N(client_spec={'template':{'execution_mode':'host','host_runtime':{'environment_name':'robotwin'}}})
    worker=N(runtime_revision=2,runtime_config={},host_environments={})
    slot=N(runtime_report={'revision':1,'applied_revision':1})
    assert '尚未应用最新运行环境配置' in slot_reason('runtime_pending',slot,job,worker)['message']
    slot.runtime_report={'revision':2,'applied_revision':2}
    assert '报告缺失或已过期' in slot_reason('runtime_pending',slot,job,worker)['message']
    result=blocker_reason('宿主机环境未批准或不可用: robotwin',worker,slot,job)
    assert 'robotwin' in result['message'] and '未在节点宿主机环境中批准' in result['message']
    worker.host_environments={'robotwin':{}}
    assert '节点检查未通过' in blocker_reason('宿主机环境未批准或不可用: robotwin',worker,slot,job)['message']
    worker.runtime_config={'images':[],'assets':{'dataset':'/private/path'}}
    asset=blocker_reason('逻辑资产未就绪: dataset',worker,slot,job)
    assert '逻辑资产「dataset」节点检查未通过' in asset['message'] and '/private/path' not in str(asset)


@pytest.mark.parametrize('field,value,code', [('max_concurrent',0,'node_concurrency'), ('cpu_capacity',0,'node_cpu'), ('ram_gb_capacity',0,'node_ram')])
def test_node_limits(env,field,value,code):
    main,c,auth=env;body,sid=setup(env)
    with main.SessionLocal() as s:
        setattr(s.get(Worker,s.get(Slot,sid).worker_id),field,value);s.commit()
    assert code in [r['code'] for r in check(env,body)['nodes'][0]['reasons']]
    c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert tick(main.SessionLocal)==0


def test_zero_pool_defaults_do_not_block_preflight_or_reservation(env):
    main,c,auth=env;body,sid=setup(env)
    with main.SessionLocal() as s:
        pool=s.get(Pool,body['pool_id'])
        pool.cpu_per_job=pool.ram_gb_per_job=0
        worker=s.get(Worker,s.get(Slot,sid).worker_id)
        worker.cpu_capacity=worker.ram_gb_capacity=0
        s.commit()
    result=check(env,body)
    assert result['status']=='ready'
    assert not {'node_cpu','node_ram'} & {r['code'] for r in result['nodes'][0]['reasons']}
    submitted=c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert submitted.status_code==201,submitted.text
    assert tick(main.SessionLocal)>0


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
