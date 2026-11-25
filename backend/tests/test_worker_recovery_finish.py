import copy
import json
from types import SimpleNamespace

import pytest

from worker.client import Worker
from worker.recovery_finish import finalize
from worker.recovery_runtime import RecoveryRuntime
from worker.storage import atomic_json


@pytest.fixture
def case(tmp_path, monkeypatch):
    proof={'generation':'g','identity':{'kind':'docker','boot_id':'boot','runtime_id':'a'*64}}
    state={'task':{'execution_id':'e'},'recovery':proof}
    calls=[]
    w=SimpleNamespace(state=state,path=tmp_path/'state',api=None)
    atomic_json(w.path,state)
    runtime={'state':'exited','exit_code':0,'identity':proof['identity']}
    monkeypatch.setattr(RecoveryRuntime,'inspect',lambda self:copy.deepcopy(runtime))
    def remove(self,expected):
        assert expected==proof['identity'];calls.append('remove');runtime['state']='missing'
    monkeypatch.setattr(RecoveryRuntime,'remove',remove)
    def post(path,body):
        calls.append((path,copy.deepcopy(body)))
        return {'status':'SUCCEEDED','cleanup_confirmed':path.endswith('/cleanup'),'duplicate':False}
    w.api=SimpleNamespace(post=post)
    return w,calls,runtime


def body():return {'status':'SUCCEEDED','exit_code':0}


def test_completion_then_remove_then_cleanup(case):
    w,calls,_=case
    assert finalize(w,body())
    assert w.state is None and json.loads(w.path.read_text()) is None
    assert [x if isinstance(x,str) else x[0].rsplit('/',1)[-1] for x in calls]==['complete','remove','cleanup']


@pytest.mark.parametrize('stage',['complete','cleanup'])
def test_lost_response_restarts_original_phase_without_start(case,stage):
    w,calls,_=case;post=w.api.post
    def lose(path,data):
        response=post(path,data)
        if path.endswith('/'+stage):raise ConnectionError('response lost')
        return response
    w.api.post=lose
    with pytest.raises(ConnectionError):finalize(w,body())
    saved=json.loads(w.path.read_text())
    assert saved['recovery_finalization']['phase']==('completion' if stage=='complete' else 'cleanup')
    w.state=saved;w.api.post=post
    w.finalize_recovery=lambda:finalize(w)
    Worker.execute(w,w.state['task'])
    assert w.state is None
    assert calls.count('remove')==1


@pytest.mark.parametrize('phase',['completion','remove','cleanup','done'])
def test_disk_failure_preserves_original_phase(case,monkeypatch,phase):
    w,calls,_=case
    real=atomic_json
    def fail(path,value):
        nextphase=value.get('recovery_finalization',{}).get('phase') if value else 'clear'
        if nextphase==phase:raise OSError('disk full')
        real(path,value)
    monkeypatch.setattr('worker.recovery_finish.atomic_json',fail)
    with pytest.raises(OSError):finalize(w,body())
    assert w.state==json.loads(w.path.read_text())
    monkeypatch.setattr('worker.recovery_finish.atomic_json',real)
    assert finalize(w,body())


def test_removal_failure_does_not_ack_cleanup(case,monkeypatch):
    w,calls,_=case
    monkeypatch.setattr(RecoveryRuntime,'remove',lambda *a:(_ for _ in ()).throw(RuntimeError('identity changed')))
    with pytest.raises(RuntimeError):finalize(w,body())
    assert w.state['recovery_finalization']['phase']=='remove'
    assert len(calls)==1 and calls[0][0].endswith('/complete')


@pytest.mark.parametrize('reply',[{},None,{'status':'RUNNING','cleanup_confirmed':False,'duplicate':False},
    {'status':'SUCCEEDED','cleanup_confirmed':0,'duplicate':False},
    {'status':'SUCCEEDED','cleanup_confirmed':False,'duplicate':0}])
def test_invalid_completion_ack_preserves_journal(case,reply):
    w,calls,_=case;w.api.post=lambda *a:reply
    with pytest.raises(ValueError):finalize(w,body())
    assert w.state['recovery_finalization']['phase']=='completion' and calls==[]


def test_false_cleanup_ack_is_not_success(case):
    w,_,_=case
    w.api.post=lambda *a:{'status':'CANCELLED','cleanup_confirmed':False,'duplicate':True}
    with pytest.raises(ValueError):finalize(w,body())
    assert w.state['recovery_finalization']['phase']=='cleanup'


@pytest.mark.parametrize('fault',['no_state','no_proof','generation','identity','body','nested_proof','running','different','exit'])
def test_invalid_initial_evidence_cannot_send_or_remove(case,fault):
    w,calls,runtime=case;data=body()
    if fault=='no_state':w.state=None
    elif fault=='no_proof':w.state.pop('recovery')
    elif fault=='generation':w.state['recovery']['generation']=''
    elif fault=='identity':w.state['recovery']['identity']['runtime_id']='bad'
    elif fault=='body':data=None
    elif fault=='nested_proof':data['recovery']={}
    elif fault=='running':runtime['state']='running'
    elif fault=='different':runtime['identity']={**runtime['identity'],'runtime_id':'b'*64}
    else:runtime['exit_code']=1
    with pytest.raises((ValueError,RuntimeError)):finalize(w,data)
    assert calls==[]


@pytest.mark.parametrize('change',['proof','body','phase'])
def test_pending_assignment_and_completion_are_immutable(case,change):
    w,_,_=case;post=w.api.post
    w.api.post=lambda *a:(_ for _ in ()).throw(ConnectionError())
    with pytest.raises(ConnectionError):finalize(w,body())
    w.api.post=post;data=None
    if change=='proof':w.state['recovery']={**w.state['recovery'],'generation':'other'}
    elif change=='body':data={**body(),'status':'FAILED'}
    else:w.state['recovery_finalization']['phase']='invalid'
    with pytest.raises(ValueError):finalize(w,data)


@pytest.mark.parametrize('host',[False,True])
def test_runtime_removal_checks_identity_and_exit(tmp_path,monkeypatch,host):
    from backend.tests.test_host_backend import worker
    from worker.host_backend import HostBackend
    w=worker(tmp_path,['python3','business.py']);w.host_mode=lambda:host
    bound={'kind':'host' if host else 'docker','boot_id':'boot','runtime_id':'a'*(32 if host else 64)}
    observed={'state':'running','identity':bound,'exit_code':None};calls=[]
    monkeypatch.setattr(RecoveryRuntime,'inspect',lambda self:observed)
    monkeypatch.setattr(HostBackend,'remove',lambda *a:calls.append('host'))
    monkeypatch.setattr('worker.client.docker',lambda *a:calls.append(a))
    r=RecoveryRuntime(w,w.state['task']['execution_id'])
    with pytest.raises(RuntimeError):r.remove(bound)
    observed.update(state='exited',exit_code=0)
    with pytest.raises(RuntimeError):r.remove({**bound,'runtime_id':'b'*len(bound['runtime_id'])})
    r.remove(bound)
    assert calls==(['host'] if host else [('rm',bound['runtime_id'])])
    observed['state']='missing';r.remove(bound)
    assert len(calls)==1


@pytest.mark.skipif(__import__('os').getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker finalization opt-in')
@pytest.mark.parametrize('mode',['host','docker'])
def test_real_runtime_finalization_after_cleanup_reply_loss(tmp_path,mode):
    import os,time
    from backend.tests.test_host_backend import worker
    from worker.host_backend import HostBackend
    from worker.client import docker
    w=worker(tmp_path,['python3','business.py'],timeout=0);w.host_mode=lambda:mode=='host'
    original=copy.deepcopy(w.state);execution=w.state['task']['execution_id'];name='mini-eval-'+execution
    script='print("finished",flush=True)'
    host=HostBackend(w);requests=[]
    try:
        if mode=='host':
            (tmp_path/'business.py').write_text(script);host.create(w.state['task'])
            path=host.root/'launch.json';launch=json.loads(path.read_text())
            launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(launch))
            host.start(w.state['task'])
        else:
            docker('run','--detach','--name',name,'--label','mini-eval.execution='+execution,
                   '--entrypoint','python',os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
            w.name=lambda e:'mini-eval-'+e;w.inspect=lambda e:Worker.inspect(w,e)
        r=RecoveryRuntime(w,execution);end=time.monotonic()+8
        while time.monotonic()<end:
            observed=r.inspect()
            if observed['state']=='exited':break
            time.sleep(.05)
        assert observed['state']=='exited' and observed['exit_code']==0
        w.state['recovery']={'generation':'g','identity':observed['identity']}
        def post(path,data):
            requests.append((path,copy.deepcopy(data)))
            assert data.get('recovery',data)==w.state['recovery']
            if path.endswith('/cleanup') and sum(x[0].endswith('/cleanup') for x in requests)==1:
                raise ConnectionError('injected lost cleanup response')
            return {'status':'SUCCEEDED','cleanup_confirmed':path.endswith('/cleanup'),'duplicate':len(requests)>2}
        w.api=SimpleNamespace(post=post)
        with pytest.raises(ConnectionError):Worker.finalize_recovery(w,body())
        assert w.state['recovery_finalization']['phase']=='cleanup'
        assert r.inspect()['state']=='exited' and r.inspect()['retained']
        assert r._raw() is None or r._raw()['State'].get('Recovered')
        w.state=json.loads(w.path.read_text())
        w.finalize_recovery=lambda:Worker.finalize_recovery(w)
        Worker.execute(w,w.state['task'])
        assert w.state is None and len(requests)==3
        assert requests[1]==requests[2]
    finally:
        w.state=original
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)


def test_deferred_results_keep_journal_after_cleanup_and_lost_sync_ack(case):
    from worker.recovery_finish import finish_results
    w,calls,_=case
    finalize(w,{'status':'SUCCEEDED','exit_code':0,'pending_results':True})
    assert w.state['recovery_finalization']['phase']=='done'
    queued=[{'body':{}}];q=SimpleNamespace(pending=lambda limit:queued)
    assert not finish_results(w,q)
    w.state['recovery_results_collected']=True
    atomic_json(w.path,w.state)
    assert not finish_results(w,q)
    queued.clear();requests=[]
    def post(path,data):
        requests.append((path,data))
        if len(requests)==1:raise TimeoutError('sync ACK lost')
        return {'status':'SUCCEEDED','cleanup_confirmed':True,'result_sync':'complete','duplicate':True}
    w.api.post=post
    with pytest.raises(TimeoutError):finish_results(w,q)
    assert w.state is not None
    w.state=json.loads(w.path.read_text())
    assert finish_results(w,q) and w.state is None
    assert requests[0]==requests[1]


def test_failed_result_sync_ack_retires_journal_without_restarting_business(case):
    from worker.recovery_finish import finish_results
    w, calls, _ = case
    finalize(w, {'status': 'SUCCEEDED', 'exit_code': 0, 'pending_results': True})
    w.state['recovery_results_collected'] = True
    atomic_json(w.path, w.state)
    w.api.post = lambda *args: {
        'status': 'SUCCEEDED', 'cleanup_confirmed': True,
        'result_sync': 'failed', 'duplicate': False}
    before = calls.count('start')
    assert finish_results(w, SimpleNamespace(pending=lambda limit: []))
    assert w.state is None and json.loads(w.path.read_text()) is None
    assert calls.count('start') == before


@pytest.mark.parametrize('fault',['missing','phase','proof','ack'])
def test_result_sync_refuses_invalid_state_or_ack(case,fault):
    from worker.recovery_finish import finish_results
    w,_,_=case
    if fault!='missing':finalize(w,{'status':'SUCCEEDED','exit_code':0,'pending_results':True})
    if fault=='phase':w.state['recovery_finalization']['phase']='completion'
    if fault=='proof':w.state['recovery']={'generation':'other','identity':w.state['recovery']['identity']}
    if fault=='ack':
        w.state['recovery_results_collected']=True
        w.api.post=lambda *args:{'result_sync':'complete'}
    with pytest.raises(ValueError):finish_results(w,SimpleNamespace(pending=lambda limit:[]))
    assert w.state is not None


@pytest.mark.parametrize('lost',['result','sync'])
def test_execute_resumes_deferred_delivery_after_cleanup_without_start(case,lost):
    from worker.recovery_results import execution_outbox
    w,calls,_=case;q=execution_outbox(w)
    digest=q.put('e','g','episode',0,{'episode_id':1,'status':'STARTED','seed':100})
    w.state['recovery_results_collected']=True
    finalize(w,{'status':'SUCCEEDED','exit_code':0,'pending_results':True})
    requests=[];dropped=False
    def post(path,data):
        nonlocal dropped
        requests.append((path,copy.deepcopy(data)))
        kind='result' if path.endswith('/results') else 'sync'
        assert path.endswith(('/results','/results-finished'))
        if kind=='result':
            assert data['apply'] is True
            response={'digest':digest,'reconciled':True,'application':'applied'}
        else:response={'status':'SUCCEEDED','cleanup_confirmed':True,'result_sync':'complete','duplicate':dropped}
        if kind==lost and not dropped:dropped=True;raise TimeoutError('lost '+lost+' ACK')
        return response
    w.api.post=post;w.finalize_recovery=lambda:finalize(w)
    with pytest.raises(TimeoutError):Worker.execute(w,w.state['task'])
    assert w.state['recovery_finalization']['phase']=='done'
    w.state=json.loads(w.path.read_text())
    Worker.execute(w,w.state['task'])
    assert w.state is None and q.pending()==[]
    assert calls.count('remove')==1
    if lost=='result':assert requests[0]==requests[1]
    else:assert requests[1]==requests[2]


def test_post_cleanup_missing_outbox_cannot_be_replaced_by_empty_queue(case):
    from worker.recovery_finish import resume_results
    w,_,_=case
    w.state['recovery_results_collected']=True
    finalize(w,{'status':'SUCCEEDED','exit_code':0,'pending_results':True})
    with pytest.raises(RuntimeError,match='outbox is missing'):resume_results(w)
    assert w.state is not None


def test_execute_keeps_uncollected_results_without_trying_to_start(case):
    w,_,_=case
    finalize(w,{'status':'FAILED','exit_code':0,'pending_results':True})
    w.finalize_recovery=lambda:finalize(w)
    Worker.execute(w,w.state['task'])
    assert w.state['recovery_finalization']['phase']=='done'
