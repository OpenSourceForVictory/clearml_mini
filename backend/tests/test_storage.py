from datetime import timedelta
import uuid
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node, submit, claim
from backend.app.core import Worker as Node, Slot, Run, now
from backend.app.scheduling import tick

CONFIG = dict(workspace_root='/srv/eval/work', assets_root='/srv/eval/assets', cache_root='/srv/eval/cache', min_free_disk_gb=2)


def setup(env, count=1, protocol=4):
    main, c, auth = env
    token, slots = node(env, count=count)
    with main.SessionLocal() as s:
        for sid in slots:
            s.get(Slot, sid).protocol_version = protocol
        w = s.scalar(select(Node))
        wid = w.id
        s.commit()
    return token, slots, f'/api/v1/admin/nodes/{wid}/storage'


def save(env, path, revision=0, config=CONFIG):
    _, c, auth = env
    return c.put(path, headers=auth['admin'], json={'expected_revision': revision, 'config': config})


def report(env, token, slot, rev=1, status='applied', execution=None, applied=None, config=CONFIG):
    body = {'slot_id': slot, 'storage': {'revision': rev, 'applied_revision': rev if applied is None else applied,
            'status': status, 'effective': {**config, 'workspace_root': config['workspace_root']+'/slots/'+slot},
            'capacity': {'workspace': 5000, 'assets': 5000, 'cache': 5000}}, 'disk': {'free_mb': 5000, 'min_free_mb': 2048}}
    if execution: body['execution_id'] = execution
    return env[1].post('/api/v1/worker/heartbeat', headers=token, json=body)


def test_storage_permissions_conflicts_and_validation(env):
    _, c, auth = env
    token, slots, path = setup(env)
    assert c.get(path, headers=auth['alice']).status_code == 403
    assert c.put(path, headers=token, json={'expected_revision':0,'config':CONFIG}).status_code == 401
    for value in ('relative', '/', '/srv/a,b', '/srv/a\n', '/srv/../etc'):
        assert save(env,path,config={**CONFIG,'assets_root':value}).status_code == 400
    for minimum in (-1, 2097152):
        assert save(env,path,config={**CONFIG,'min_free_disk_gb':minimum}).status_code == 400
    assert save(env,path).json()['revision'] == 1
    assert save(env,path).status_code == 409
    assert save(env,path,1,None).json()['config'] is None
    assert c.get(path,headers=auth['admin']).json()['revision'] == 2


def test_pending_releases_reservations_but_preserves_claimed_and_cleanup(env):
    main,c,auth=env
    token,slots,path=setup(env,protocol=3)
    job=submit(env)
    assert tick(main.SessionLocal)==1
    assert save(env,path).status_code==200
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.job_id==job))
        assert run.status=='PENDING' and run.slot_id is None and run.reserved_cpu==0
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).protocol_version=4;s.commit()
    assert report(env,token,slots[0]).status_code==200
    task=claim(env,token,slots[0])
    assert task['storage_revision']==1
    assert save(env,path,1).status_code==200
    assert report(env,token,slots[0],2).status_code==409
    # Child reports renew their lease, but cannot acknowledge storage.
    assert report(env,token,slots[0],2,execution=task['execution_id']).status_code==200
    assert c.get(path,headers=auth['admin']).json()['slots'][0]['applied_revision']==1
    # Recovery remains possible while a new storage configuration is pending.
    assert claim(env,token,slots[0])['execution_id']==task['execution_id']
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.execution_id==task['execution_id']))
        run.status='FAILED';run.cleanup_confirmed=False;s.commit()
    assert report(env,token,slots[0],2).status_code==409
    assert c.post(f"/api/v1/worker/executions/{task['execution_id']}/cleanup",headers=token).status_code==200
    assert report(env,token,slots[0],2).status_code==200


def test_per_card_stale_ack_offline_and_disk_recovery(env):
    main,c,auth=env
    token,slots,path=setup(env,2)
    assert save(env,path).status_code==200
    assert report(env,token,slots[0]).status_code==200
    assert report(env,token,slots[1],status='waiting',applied=0).status_code==200
    submit(env);submit(env)
    assert tick(main.SessionLocal)==1
    assert save(env,path,1).status_code==200
    assert report(env,token,slots[0],rev=1).status_code==200
    assert tick(main.SessionLocal)==0
    assert report(env,token,slots[0],rev=2).status_code==200
    c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'disk':{'free_mb':0,'min_free_mb':2048}})
    assert tick(main.SessionLocal)==0
    assert report(env,token,slots[0],rev=2).status_code==200
    assert tick(main.SessionLocal)==1
    # A fresh execution heartbeat cannot refresh stale supervisor storage status.
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.storage_reported_at=now()-timedelta(seconds=20);s.commit()
    data=c.get(path,headers=auth['admin']).json()
    assert data['slots'][0]['status']=='offline' and not data['all_applied']
