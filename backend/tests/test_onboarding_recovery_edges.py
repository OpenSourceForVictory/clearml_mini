"""Enrollment recovery and authorization boundaries; SSH is a fixture transport."""
import copy
import json

import pytest
from backend.tests.test_ssh_onboarding import operations,form,wait
from onboarding import executor
from onboarding.contracts import Rejected


@pytest.mark.parametrize('state',['enrolling','unmanaged','online'])
def test_recover_existing_identity_only_when_not_managed(operations,monkeypatch,state):
    ops,calls=operations;Original=executor.Control
    class Control(Original):
        def call(self,path,body=None):
            if path=='admin/nodes':return [{'id':'existing','node_name':'ssh-test','status':state,'management_revision':4}]
            return super().call(path,body)
    monkeypatch.setattr(executor,'Control',Control)
    job=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer fixture')
    final=wait(lambda:ops.jobs[job['id']] if ops.jobs[job['id']]['state'] in ('READY','FAILED') else None)
    if state=='online':assert final['error']=='node_already_managed' and job['id'] not in ops.secrets
    else:assert final['form']['node_id']=='existing' and final['form']['expected_revision']==4


@pytest.mark.parametrize('mode',['cancel','wrong-owner','active-cancel','confirm-before-check','expired','history-expired'])
def test_operation_authorization_expiry_and_deletion(operations,mode):
    ops,calls=operations;job=ops.start('admin',form(),'Bearer fixture')
    identity=job['id'];wait(lambda:ops.jobs[identity]['state']=='NEEDS_HOST_KEY')
    if mode=='wrong-owner':
        with pytest.raises(Rejected,match='operation_not_found'):ops.cancel(identity,'other')
        assert identity in ops.secrets
    elif mode=='active-cancel':
        ops.update(identity,'INSTALLING')
        with pytest.raises(Rejected,match='cannot_cancel'):ops.cancel(identity,'admin')
    elif mode=='confirm-before-check':
        with pytest.raises(Rejected,match='environment_check_required'):ops.confirm(identity,'admin')
    elif mode=='expired':
        ops.clock=lambda:job['expires_at']+1
        ops.expire();assert ops.jobs[identity]['state']=='EXPIRED' and identity not in ops.secrets
    else:
        ops.cancel(identity,'admin');assert identity not in ops.secrets
        if mode=='history-expired':
            timestamp=ops.jobs[identity]['updated_at'];ops.clock=lambda:timestamp+86401
            assert ops.list('admin')['operations']==[] and not (ops.root/(identity+'.json')).exists()
        else:assert ops.jobs[identity]['state']=='CANCELLED'


@pytest.mark.parametrize('mode',['existing','cleanup-warning','already-complete','reconnect-timeout','invalid-temp','resource-rejected'])
def test_install_recovery_edges(operations,monkeypatch,mode):
    ops,calls=operations
    plan=ops.probe(None,None,None)
    if mode=='existing':plan.update(existing=True,services=[{'scope':'user','name':'worker.service'}])
    ops.probe=lambda *a:copy.deepcopy(plan)
    OriginalRemote=ops.remote_factory;remote_calls=[]
    class Remote(OriginalRemote):
        def run(self,argv,*a,**kw):
            remote_calls.append(argv)
            if argv[0]=='rm' and mode=='cleanup-warning':raise OSError('cleanup unavailable')
            if argv[0]=='mktemp' and mode=='invalid-temp':return 0,b'/etc/forbidden',b''
            return super().run(argv,*a,**kw)
    ops.remote_factory=Remote
    OriginalControl=executor.Control
    class Control(OriginalControl):
        def call(self,path,body=None):
            if path.endswith('/enrollment') and mode in ('already-complete','reconnect-timeout'):
                return {'ready':False,'state':'COMPLETE' if mode=='already-complete' else 'INSTALLING','revision':1}
            return super().call(path,body)
    monkeypatch.setattr(executor,'Control',Control)
    job=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer fixture');identity=job['id']
    wait(lambda:ops.jobs[identity]['state']=='READY')
    if mode=='resource-rejected':ops.host_resources=lambda *a:{'ready':False}
    if mode=='reconnect-timeout':
        clock=[0]
        def tick():clock[0]+=70;return clock[0]
        monkeypatch.setattr(executor.time,'monotonic',tick)
        monkeypatch.setattr(executor.time,'sleep',lambda delay:None)
    # Run deterministically after the check worker completed; no concurrent fake clocks.
    ops.update(identity,'INSTALLING');ops.install(identity)
    final=ops.jobs[identity]
    expected={'invalid-temp':'invalid_remote_temporary_directory','resource-rejected':'runtime_limits_not_enforced','reconnect-timeout':'component_reconnect_timeout'}
    if mode in expected:assert final['state']=='FAILED' and final['error']==expected[mode]
    else:assert final['state']=='SUCCEEDED'
    if mode=='existing':assert any('--existing' in argv and 'user:worker.service' in argv for argv in remote_calls)
    if mode=='cleanup-warning':assert final['cleanup_warning'] is True
    assert identity not in ops.secrets
    assert 'SECRET' not in json.dumps(final)


@pytest.mark.parametrize('changed',[False,True])
def test_checked_layout_transferred_and_drift_prevents_identity_rotation(operations,changed):
    ops,calls=operations;plan=ops.probe(None,None,None)
    plan.update(existing=True,installation={'layout':{'app_dir':'/opt/original'},'fingerprint':'first'})
    ops.probe=lambda *a:copy.deepcopy(plan)
    Original=ops.remote_factory;uploads={};commands=[]
    class Remote(Original):
        def upload(self,path,payload):uploads[path]=payload
        def run(self,argv,*a,**kw):commands.append(argv);return super().run(argv,*a,**kw)
    ops.remote_factory=Remote
    job=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer fixture');identity=job['id']
    wait(lambda:ops.jobs[identity]['state']=='READY')
    if changed:plan['installation']['fingerprint']='changed'
    ops.update(identity,'INSTALLING');ops.install(identity)
    if changed:
        assert ops.jobs[identity]['error']=='environment_changed_repeat_check'
        assert not any(path.endswith('/begin') for path,_ in calls)
        assert not uploads
    else:
        assert ops.jobs[identity]['state']=='SUCCEEDED'
        assert json.loads(uploads['/tmp/mini-eval-node.ABCDEFGH/layout.json'])==plan['installation']
        assert any('--layout-snapshot' in cmd and '--existing' in cmd for cmd in commands)
