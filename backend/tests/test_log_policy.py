import json
from types import SimpleNamespace
import pytest
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Slot,Worker,Run,ExecutionLogPolicy
from backend.app.log_policy import snapshot,merge_software
from runner.process_log_contract import DEFAULT_POLICY
from worker.log_policy import LogPolicy


def test_node_policy_receipts_permissions_and_snapshot(env):
    main,c,auth=env;token,slots=node(env,count=2);other,other_slots=node(env,'other')
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);identity=slot.worker_id
        slot.software={'capabilities':['process_log_policy_v1']};s.commit()
    base=f'/api/v1/admin/nodes/{identity}/log-policy';sync=f'/api/v1/worker/slots/{slots[0]}/log-policy'
    assert c.get(base,headers=auth['alice']).status_code==403
    assert c.get('/api/v1/admin/nodes/missing/log-policy',headers=auth['admin']).status_code==404
    assert c.put('/api/v1/admin/nodes/missing/log-policy',headers=auth['admin'],json={'expected_revision':0,'policy':DEFAULT_POLICY}).status_code==404
    view=c.get(base,headers=auth['admin']).json();assert view['revision']==0 and len(view['slots'])==2
    assert c.post(sync,headers=token,json={}).json()['policy']==DEFAULT_POLICY
    assert c.post(sync,headers=other,json={}).status_code==404
    assert c.post(sync,headers=token,json={'bad':1}).status_code==422
    policy={**DEFAULT_POLICY,'scope_bytes':32*1024**2}
    body={'expected_revision':0,'policy':policy}
    assert c.put(base,headers=auth['admin'],json={**body,'policy':{**policy,'node_bytes':10}}).status_code==400
    assert c.put(base,headers=auth['admin'],json=body).json()['revision']==1
    assert c.put(base,headers=auth['admin'],json=body).status_code==409
    receipt={'revision':1,'status':'applied','policy':policy}
    assert c.post(sync,headers=token,json={**receipt,'revision':0}).status_code==409
    assert c.post(sync,headers=token,json={**receipt,'policy':DEFAULT_POLICY}).status_code==409
    assert c.post(f'/api/v1/worker/slots/{slots[1]}/log-policy',headers=token,json=receipt).status_code==409
    assert c.post(sync,headers=token,json=receipt).status_code==200
    assert c.post(sync,headers=token,json={'revision':1,'status':'failed','error':'disk full'}).status_code==200
    view=c.get(base,headers=auth['admin']).json();r=view['slots'][0]['report']
    assert r['status']=='failed' and r['applied_revision']==1 and r['policy']==policy
    heartbeat=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'software':{'version':'test','capabilities':['process_log_policy_v1']}})
    assert heartbeat.status_code==200,heartbeat.text
    assert c.get(base,headers=auth['admin']).json()['slots'][0]['report']==r
    submit(env);task=claim(env,token,slots[0])
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);old=slot.software
        assert merge_software(old,{'version':'new'})['process_log_policy']==r
        assert merge_software(old,None) is None
        assert merge_software({}, {})=={}
        # Snapshot immutability is exercised independently of the scheduler's resource admission.
        run=s.get(Run,task['run_id'])
        first=snapshot(s,run,slot);assert first=={'revision':1,'policy':policy}
        slot.software={**old,'process_log_policy':{'applied_revision':2,'policy':DEFAULT_POLICY}}
        assert snapshot(s,run,slot)==first
        assert snapshot(s,run,s.get(Slot,slots[1])) is None
        s.delete(s.get(ExecutionLogPolicy,run.id));s.commit()


def test_worker_policy_persistence_and_failed_update(tmp_path,monkeypatch):
    calls=[];desired={'revision':1,'policy':DEFAULT_POLICY}
    def post(path,body=None):
        calls.append(body)
        return desired
    w=SimpleNamespace(path=tmp_path/'worker.json',slot_id='slot',config={},api=SimpleNamespace(post=post))
    service=LogPolicy(w);assert service.applied is None
    service.sync();assert calls[-1]['status']=='applied' and w.config['process_log_policy_revision']==1
    assert LogPolicy(w).applied==desired
    service.sync();assert calls[-1]['status']=='applied'
    desired={'revision':2,'policy':{**DEFAULT_POLICY,'scope_bytes':64*1024**2}}
    def denied(*a):raise PermissionError('private')
    monkeypatch.setattr('worker.log_policy.atomic_json',denied)
    service.sync();assert calls[-1]['status']=='failed' and service.applied['revision']==1
    assert w.config['process_log_policy_revision']==1
    desired={'revision':2,'policy':None};service.sync();assert calls[-1]['status']=='failed'
    desired={'revision':-1,'policy':DEFAULT_POLICY};service.sync();assert calls[-1] is None
    service.path.write_text(json.dumps({'revision':False,'policy':DEFAULT_POLICY}));assert LogPolicy(w).applied is None
    service.path.write_text('invalid');assert LogPolicy(w).applied is None


def test_execution_log_budget_is_frozen_across_worker_recovery(tmp_path):
    import uuid
    from worker.process_logs import prepare_checked
    from runner.process_log_store import locked,read_json
    calls=[]
    worker=SimpleNamespace(path=tmp_path/'state',slot_id='s',config={},api=SimpleNamespace(post=lambda p,b:calls.append(b)))
    execution=str(uuid.uuid4());root=tmp_path/'runs'/execution/'logs'
    policy={**DEFAULT_POLICY,'scope_bytes':32*1024**2}
    task={'execution_id':execution,'process_logs_v1':True,'process_log_policy':{'revision':7,'policy':policy}}
    first=prepare_checked(worker,task,root)
    task['process_log_policy']={'revision':8,'policy':DEFAULT_POLICY}
    assert prepare_checked(worker,task,root)==first
    assert [x['policy_revision'] for x in calls]==[7,7]
    with locked(root,False) as fd:
        manifest=read_json(fd,'manifest.json')
        assert manifest['policy']['scope_bytes']==policy['scope_bytes']


def test_policy_sync_failure_does_not_block_historical_reads(tmp_path,monkeypatch):
    from worker.process_logs import LogService
    worker=SimpleNamespace(path=tmp_path/'state',config={},children={})
    service=LogService(worker);calls=[];clock=iter([0,0,1,31,31])
    monkeypatch.setattr('worker.process_logs.time.monotonic',lambda:next(clock))
    def sync():calls.append('policy');raise OSError('offline')
    service.policy.sync=sync
    def cycle():
        calls.append('read')
        if calls.count('read')==3:service.stopping.set()
    service.cycle=cycle
    monkeypatch.setattr(service.stopping,'wait',lambda seconds:None)
    service.loop()
    assert calls==['policy','read','read','policy','read']
