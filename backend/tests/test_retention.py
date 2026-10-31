from datetime import timedelta
import uuid
from sqlalchemy import select
from backend.app.core import Slot, Run, Worker, ExecutionData, DataCleanup, DataCleanupItem, now
from backend.tests.test_execution import env, node, submit, claim


def setup(env):
    main, c, auth = env
    token, slots = node(env)
    job = submit(env)
    task = claim(env, token, slots[0])
    ex = task['execution_id']
    assert c.post(f'/api/v1/worker/executions/{ex}/complete', headers=token, json={'status':'FAILED','error':'test'}).status_code == 200
    with main.SessionLocal() as s:
        slot = s.get(Slot, slots[0]); slot.protocol_version = 6
        worker = slot.worker_id
        run = s.scalar(select(Run).where(Run.execution_id == ex)); run.finished_at = now()-timedelta(days=40)
        s.commit()
    base=f'/api/v1/admin/nodes/{worker}'
    control=f'/api/v1/worker/slots/{slots[0]}/execution-data'
    entry={'root_id':'a'*64,'root_path':'/trusted/runs','entry_name':ex,'generation':str(uuid.uuid4()),'scan_status':'complete',
           'identity':{'root_dev':1,'root_ino':2,'dev':1,'ino':3,'fingerprint':'b'*64},'logical_bytes':100,'allocated_bytes':4096}
    assert c.post(control+'/inventory',headers=token,json={'entries':[entry]}).status_code==200
    data=c.get(base+'/execution-data',headers=auth['admin']).json()['items'][0]
    return main,c,auth,token,slots[0],base,control,entry,data,job


def preview(c,auth,base,data):
    r=c.post(base+'/execution-data/preview',headers=auth['admin'],json={'entries':[{'id':data['id'],'generation':data['generation']}]})
    assert r.status_code==200,r.text
    return r.json()


def test_preview_confirm_receipt_permissions_and_artifacts(env):
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    assert c.get(base+'/execution-data',headers=auth['alice']).status_code==403
    assert c.post(control+'/inventory',headers=auth['admin'],json={'entries':[]}).status_code==401
    other,_=node(env,'other')
    assert c.post(control+'/control',headers=other).status_code==404
    assert c.get(base+'/execution-data?limit=101',headers=auth['admin']).status_code==400
    assert c.post(control+'/inventory',headers=token,json={'entries':[entry]*101}).status_code==400
    op=preview(c,auth,base,data)
    url=base+f'/execution-data/operations/{op["id"]}/confirm'
    for _ in range(2):
        response=c.post(url,headers=auth['admin']); assert response.status_code==202,response.text
        assert response.json()['id']==op['id']
    item=c.post(control+'/control',headers=token).json()['items'][0]
    assert c.post(control+f'/{item["id"]}/begin',headers=token).status_code==200
    receipt={'status':'succeeded','deleted_entries':3,'free_before':100,'free_after':200}
    for _ in range(2):
        assert c.post(control+f'/{item["id"]}/receipt',headers=token,json=receipt).status_code==200
    result=c.get(f'/api/v1/jobs/{job}/execution-data',headers=auth['alice']).json()
    assert result==[{'execution_id':entry['entry_name'],'status':'succeeded'}]
    assert c.get(f'/api/v1/jobs/{job}/execution-data',headers=auth['bob']).status_code==404
    assert c.get(f'/api/v1/jobs/{job}',headers=auth['alice']).json()['status']=='FAILED'


def test_stale_partial_unknown_foreign_and_old_worker(env):
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    original=data['allocated_bytes']
    response=c.post(control+'/inventory',headers=token,json={'entries':[{**entry,'scan_status':'partial','identity':None,'allocated_bytes':None}]})
    assert response.status_code==200,response.text
    value=c.get(base+'/execution-data',headers=auth['admin']).json()['items'][0]
    assert value['allocated_bytes']==original and value['blocked_reason']
    assert preview(c,auth,base,data)['items'][0]['status']=='skipped'
    c.post(control+'/inventory',headers=token,json={'entries':[entry]})
    with main.SessionLocal() as s:
        d=s.get(ExecutionData,data['id']);d.last_complete_at=now()-timedelta(days=2);s.commit()
    assert '24 hours' in preview(c,auth,base,data)['items'][0]['reason']
    other,slots=node(env,'other')
    with main.SessionLocal() as s:
        s.get(Slot,slots[0]).protocol_version=6;s.commit()
    assert c.post(f'/api/v1/worker/slots/{slots[0]}/execution-data/inventory',headers=other,json={'entries':[entry]}).status_code==409
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=5;s.commit()
    assert c.post(control+'/control',headers=token).status_code==409
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot}).status_code==200


def test_policy_version_disable_and_hourly_dedup(env):
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    assert c.get(base+'/execution-data',headers=auth['admin']).json()['policy']['enabled'] is False
    assert c.post(control+'/control',headers=token).json()['items']==[]
    body={'expected_revision':0,'enabled':True,'days':30}
    p=c.post(base+'/retention/preview',headers=auth['admin'],json=body).json()
    assert c.put(base+'/retention',headers=auth['admin'],json={**body,'preview_id':p['id']}).status_code==200
    items=c.post(control+'/control',headers=token).json()['items'];assert len(items)==1
    assert c.post(control+'/control',headers=token).json()['items']==items
    assert c.put(base+'/retention',headers=auth['admin'],json=body).status_code==409
    item=items[0]
    assert c.post(control+f'/{item["id"]}/begin',headers=token).status_code==200
    assert c.put(base+'/retention',headers=auth['admin'],json={'expected_revision':1,'enabled':False,'days':30}).status_code==200
    # Authorized work may finish offline/after disable, but cannot acquire a new start.
    assert c.post(control+f'/{item["id"]}/begin',headers=token).status_code==409
    assert c.post(control+f'/{item["id"]}/receipt',headers=token,json={'status':'succeeded'}).status_code==200


def test_preview_expiry_generation_storage_and_active_guards(env):
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    op=preview(c,auth,base,data)
    with main.SessionLocal() as s:
        s.get(DataCleanup,op['id']).expires_at=now()-timedelta(seconds=1);s.commit()
    assert c.post(base+f'/execution-data/operations/{op["id"]}/confirm',headers=auth['admin']).status_code==409
    op=preview(c,auth,base,data)
    with main.SessionLocal() as s:
        w=s.get(Worker,s.get(Slot,slot).worker_id);w.storage_revision+=1;s.commit()
    assert c.post(base+f'/execution-data/operations/{op["id"]}/confirm',headers=auth['admin']).status_code==409
    with main.SessionLocal() as s:
        w=s.get(Worker,s.get(Slot,slot).worker_id);w.storage_revision=0
        run=s.scalar(select(Run).where(Run.execution_id==entry['entry_name']));run.cleanup_confirmed=False;s.commit()
    assert preview(c,auth,base,data)['items'][0]['reason']=='execution or container cleanup not finished'


def test_retry_keeps_old_execution_inventory_and_offline_never_starts(env):
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    old=entry['entry_name']
    op=preview(c,auth,base,data)
    assert c.post(f'/api/v1/jobs/{job}/retry',headers=auth['alice']).status_code==202
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.job_id==job).order_by(Run.attempt)))
        assert len(runs)==2 and runs[0].execution_id==old and runs[1].execution_id is None
    assert c.post(base+f'/execution-data/operations/{op["id"]}/confirm',headers=auth['admin']).status_code==202
    item=c.post(control+'/control',headers=token).json()['items'][0]
    with main.SessionLocal() as s:s.get(Slot,slot).last_seen_at=now()-timedelta(minutes=1);s.commit()
    assert c.post(control+f'/{item["id"]}/begin',headers=token).status_code==409
    assert c.get(f'/api/v1/jobs/{job}',headers=auth['alice']).json()['status']=='PENDING'
