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
        slot=s.get(Slot,slots[0]);slot.storage_reported_at=now()-timedelta(seconds=61);s.commit()
    data=c.get(path,headers=auth['admin']).json()
    assert data['slots'][0]['status']=='offline' and not data['all_applied']


def test_independent_device_storage_preserves_machine_defaults_and_revisions(env):
    main,c,auth=env
    token,slots,path=setup(env,2)
    other={**CONFIG,'workspace_root':'/srv/eval/node-a/gpu1'}
    slot_path=f'{path}/slots/{slots[1]}'
    assert c.put(slot_path,headers=auth['alice'],json={'expected_revision':0,'config':other}).status_code==403
    assert c.put(path+'/slots/missing',headers=auth['admin'],json={'expected_revision':0,'config':other}).status_code==404
    result=c.put(slot_path,headers=auth['admin'],json={'expected_revision':0,'config':other})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['revision']==1 and data['config'] is None
    assert data['slots'][0]['config'] is None and data['slots'][1]['config']==other
    assert c.put(slot_path,headers=auth['admin'],json={'expected_revision':0,'config':other}).status_code==409
    for slot,expected in ((slots[0],None),(slots[1],other)):
        reply=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot}).json()
        assert reply['storage']=={'revision':1,'config':expected}
    assert report(env,token,slots[0],config=CONFIG).status_code==200
    assert report(env,token,slots[1],config=CONFIG).status_code==409
    assert report(env,token,slots[1],config=other).status_code==200
    assert save(env,path,1,CONFIG).status_code==200
    reply=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[1]}).json()
    assert reply['storage']['config']==other
    assert c.put(slot_path,headers=auth['admin'],json={'expected_revision':2,'config':None}).status_code==200
    reply=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[1]}).json()
    assert reply['storage']['config']==CONFIG
    with main.SessionLocal() as s:
        assert s.scalar(select(Node)).storage_config==CONFIG


@pytest.mark.parametrize('card_root',[None,'/srv/card-artifacts'])
def test_card_artifact_inheritance_ack_and_machine_changes(env,card_root):
    from backend.app.storage import desired, OVERRIDES
    main,c,auth=env
    token,slots,path=setup(env,count=2,protocol=17)
    machine={**CONFIG,'artifact_root':'/srv/machine-artifacts'}
    assert save(env,path,config=machine).status_code==200
    card={**CONFIG,'workspace_root':'/srv/card-work','artifact_root':card_root}
    assert save(env,path+'/slots/'+slots[0],1,card).status_code==200
    with main.SessionLocal() as s:
        w=s.scalar(select(Node));slot=s.get(Slot,slots[0])
        original=dict(w.connection[OVERRIDES][slot.id])
        expected={**card,'artifact_root':card_root or machine['artifact_root']}
        assert desired(w,slot)['config']==expected
        assert w.connection[OVERRIDES][slot.id]==original
        for sid in slots:s.get(Slot,sid).software={'capabilities':['artifact_roots_v1']}
        s.commit()
    # Acknowledgement must use the merged directory, not the saved raw override.
    if card_root is None:
        assert report(env,token,slots[0],rev=2,config=card).status_code==409
    response=report(env,token,slots[0],rev=2,config=expected)
    assert response.status_code==200
    assert response.json()['storage']['config']==expected
    assert report(env,token,slots[1],rev=2,config=machine).status_code==200
    for revision,root in [(2,'/srv/new-artifacts'),(3,None)]:
        next_machine={**CONFIG,**({'artifact_root':root} if root else {})}
        assert save(env,path,revision,next_machine).status_code==200
        effective={**card}
        if card_root is None:
            effective.pop('artifact_root')
            if root:effective['artifact_root']=root
        reply=report(env,token,slots[0],rev=revision+1,config=effective)
        assert reply.status_code==200
        assert reply.json()['storage']['config']==effective
        view=c.get(path,headers=auth['admin']).json()
        raw=next(x for x in view['slots'] if x['slot_id']==slots[0])['config']
        assert raw.get('artifact_root')==card_root


def test_card_artifact_inheritance_does_not_modify_saved_null():
    from types import SimpleNamespace
    from backend.app.storage import desired,OVERRIDES
    card={**CONFIG,'artifact_root':None}
    w=SimpleNamespace(storage_config={**CONFIG,'artifact_root':'/srv/results'},storage_revision=9,
                      connection={OVERRIDES:{'a':card}})
    assert desired(w,SimpleNamespace(id='a'))['config']['artifact_root']=='/srv/results'
    assert card['artifact_root'] is None
    w.storage_config=None
    assert desired(w,SimpleNamespace(id='a'))['config']==card


def test_old_applied_card_without_inherited_root_waits_for_resave(env):
    from backend.app.storage import ready,OVERRIDES
    main,c,auth=env
    token,slots,path=setup(env,protocol=17)
    assert save(env,path).status_code==200
    assert report(env,token,slots[0]).status_code==200
    # Simulate a persisted pre-upgrade card override at the same revision.
    with main.SessionLocal() as s:
        w=s.scalar(select(Node));slot=s.get(Slot,slots[0])
        w.storage_config={**CONFIG,'artifact_root':'/srv/shared-results'}
        w.connection={**(w.connection or {}),OVERRIDES:{slot.id:dict(CONFIG)}}
        slot.software={'capabilities':['artifact_roots_v1']};s.commit()
        assert not ready(w,slot,now())
    view=c.get(path,headers=auth['admin']).json()
    assert view['slots'][0]['status']=='pending' and not view['all_applied']
    assert save(env,path,1,{**CONFIG,'artifact_root':'/srv/shared-results'}).status_code==200
    assert report(env,token,slots[0],rev=2,config={**CONFIG,'artifact_root':'/srv/shared-results'}).status_code==200
    with main.SessionLocal() as s:
        assert ready(s.scalar(select(Node)),s.get(Slot,slots[0]),now())
