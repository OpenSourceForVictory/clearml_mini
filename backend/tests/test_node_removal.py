"""Node tombstones release live identity without rewriting historical IDs."""
import pytest
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Worker,Slot,Run,ManagementAudit
from sqlalchemy import select


def unmanaged(env):
    main,c,a=env
    token,slots=node(env)
    item=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    prefix='/api/v1/admin/nodes/'+item['id']
    assert c.post(prefix+'/unmanage',headers=a['admin'],json={'expected_revision':0}).status_code==200
    return item,prefix,token,slots


def test_removal_reuses_name_and_device_identity_and_retains_records(env):
    main,c,a=env;item,prefix,token,slots=unmanaged(env)
    assert c.request('DELETE',prefix,headers=a['admin'],json={'expected_revision':1}).status_code==200
    assert c.get('/api/v1/admin/nodes',headers=a['admin']).json()==[]
    assert c.get('/api/v1/workers',headers=a['admin']).json()==[]
    with main.SessionLocal() as s:
        assert s.get(Worker,item['id']).deleted_at
        assert s.get(Slot,slots[0]).deleted_at
        assert s.scalar(select(ManagementAudit).where(ManagementAudit.target_id==item['id'],ManagementAudit.action=='machine.delete'))
    newtoken,newslots=node(env)
    assert newslots!=slots and newtoken!=token
    assert c.get(prefix+'/enrollment',headers=a['admin']).status_code==404
    assert c.post(prefix+'/unmanage',headers=a['admin'],json={'expected_revision':2}).status_code==404
    assert c.patch(prefix,headers=a['admin'],json={'enabled':True}).status_code==404


@pytest.mark.parametrize('case',['managed','revision','permission','upgrade','cleanup','execution','pending'])
def test_removal_rejects_unsafe_state(env,case):
    main,c,a=env;item,prefix,token,slots=unmanaged(env)
    with main.SessionLocal() as s:
        w=s.get(Worker,item['id'])
        if case=='managed':w.revoked_at=None
        if case=='upgrade':w.upgrade_state='FAILED'
        if case=='cleanup':w.connection={'node_cleanup':{'state':'RUNNING'}}
        if case in ('execution','pending'):
            # Existing fixture supplies valid job/FK dependencies.
            pass
        s.commit()
    if case in ('execution','pending'):
        job=submit(env)
        with main.SessionLocal() as s:
            run=s.scalar(select(Run))
            if case=='execution':run.slot_id=slots[0];run.cleanup_confirmed=False
            else:
                from backend.app.core import Job
                s.get(Job,run.job_id).target_slot_id=slots[0]
            s.commit()
    response=c.request('DELETE',prefix,headers=a['alice' if case=='permission' else 'admin'],json={'expected_revision':0 if case=='revision' else 1})
    assert response.status_code==(403 if case=='permission' else 409),response.text
    with main.SessionLocal() as s:assert s.get(Worker,item['id']).deleted_at is None


def test_cleanup_receipts_gate_deletion_and_cannot_be_forged(env,monkeypatch):
    main,c,a=env;item,prefix,_,_=unmanaged(env)
    operation='a'*32
    assert c.post(prefix+'/cleanup/eligibility',headers=a['admin'],json={'expected_revision':1}).status_code==200
    assert c.post(prefix+'/cleanup/begin',headers=a['admin'],json={'expected_revision':1,'operation_id':operation}).status_code==200
    assert c.post(prefix+'/cleanup/begin',headers=a['admin'],json={'expected_revision':2,'operation_id':'b'*32}).status_code==409
    assert c.request('DELETE',prefix,headers=a['admin'],json={'expected_revision':2}).status_code==409
    monkeypatch.setattr('backend.app.ssh_onboarding.executor',lambda *args:{'operations':[]})
    assert c.post(prefix+'/cleanup/complete',headers=a['admin'],json={'operation_id':operation,'state':'SUCCEEDED'}).status_code==409
    result={'id':operation,'kind':'cleanup','node_id':item['id'],'state':'FAILED','error':'control_api_rejected_401','cleanup_result':{'state':'SUCCEEDED','backup':'/root/mini-eval-node-backups/'+operation}}
    monkeypatch.setattr('backend.app.ssh_onboarding.executor',lambda *args:{'operations':[result]})
    assert c.get('/api/v1/admin/ssh-onboarding',headers=a['admin']).status_code==200
    assert c.request('DELETE',prefix,headers=a['admin'],json={'expected_revision':3}).status_code==200


def test_confirm_passes_current_authorization(env,monkeypatch):
    _,c,a=env;calls=[]
    monkeypatch.setattr('backend.app.ssh_onboarding.executor',lambda path,body:calls.append((path,body)) or {})
    assert c.post('/api/v1/admin/ssh-onboarding/op/confirm',headers=a['admin']).status_code==200
    assert calls[0][1]['authorization']==a['admin']['Authorization']


def test_cleanup_form_uses_registered_identity_and_requires_root(env,monkeypatch):
    _,c,a=env;item,prefix,_,_=unmanaged(env);calls=[]
    from backend.tests.test_ssh_onboarding import form
    monkeypatch.setattr('backend.app.ssh_onboarding.executor',lambda path,body:calls.append((path,body)) or {'id':'test'})
    body={**form(),'expected_revision':1}
    assert c.post(prefix+'/cleanup/check',headers=a['alice'],json=body).status_code==403
    assert c.post(prefix+'/cleanup/check',headers=a['admin'],json=body).status_code==400
    body['username']='root';body['node_name']='spoofed'
    assert c.post(prefix+'/cleanup/check',headers=a['admin'],json=body).status_code==200
    assert calls[0][0]=='/cleanup-check' and calls[0][1]['form']['node_name']==item['node_name']
    body['port']='invalid'
    assert c.post(prefix+'/cleanup/check',headers=a['admin'],json=body).status_code==400


def test_failed_cleanup_is_reconciled_and_requires_new_check(env,monkeypatch):
    main,c,a=env;item,prefix,_,_=unmanaged(env);operation='a'*32
    assert c.post(prefix+'/cleanup/begin',headers=a['admin'],json={'expected_revision':1,'operation_id':operation}).status_code==200
    job={'id':operation,'kind':'cleanup','form':{'node_id':item['id']},'state':'INTERRUPTED','error':'executor_restarted_credentials_expired'}
    monkeypatch.setattr('backend.app.ssh_onboarding.executor',lambda *args:{'operations':[job]})
    c.get('/api/v1/admin/ssh-onboarding',headers=a['admin'])
    assert c.request('DELETE',prefix,headers=a['admin'],json={'expected_revision':3}).status_code==409
    assert c.post(prefix+'/reenroll',headers=a['admin'],json={'expected_revision':3}).status_code==409
    assert c.post(prefix+'/cleanup/begin',headers=a['admin'],json={'expected_revision':3,'operation_id':'b'*32}).status_code==200


def test_successful_history_keeps_original_slot_after_identity_reuse(env):
    main,c,a=env;item,prefix,_,slots=unmanaged(env);submit(env)
    with main.SessionLocal() as s:
        from backend.app.core import Job
        run=s.scalar(select(Run));run.slot_id=slots[0];run.status='SUCCEEDED';run.cleanup_confirmed=True
        s.get(Job,run.job_id).status='SUCCEEDED';run_id=run.id;s.commit()
    assert c.request('DELETE',prefix,headers=a['admin'],json={'expected_revision':1}).status_code==200
    _,newslots=node(env)
    with main.SessionLocal() as s:
        run=s.get(Run,run_id)
        assert run.slot_id==slots[0] and run.slot_id not in newslots
        assert s.get(Slot,run.slot_id).worker_id==item['id']
