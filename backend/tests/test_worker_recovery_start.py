import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest

from worker.recovery_start import start_or_recover as prepare
from worker.recovery_runtime import RecoveryRuntime
from worker.storage import atomic_json


@pytest.fixture
def case(tmp_path,monkeypatch):
    execution=str(uuid.uuid4());boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    task={'execution_id':execution}
    receipt={'execution_id':execution,'generation':'g','request_id':str(uuid.uuid4()),'disposition':'reconcile','task':copy.deepcopy(task)}
    state={'task':task,'created':False,'recovery_assignment':{'reconciliation_required':True,
        'claim':{'slot_id':'s','boot_id':boot,'receipt':receipt}}}
    worker=SimpleNamespace(path=tmp_path/'state',state=state,slot_id='s',start_lock=threading.Lock(),host_mode=lambda:False)
    atomic_json(worker.path,state)
    raw=[None];calls=[]
    process={'kind':'docker','boot_id':boot,'runtime_id':'a'*64}
    def create(task):
        calls.append('create')
        raw[0]={'Id':'a'*64,'State':{'Status':'created','Running':False},'Config':{'Labels':{'mini-eval.launch':task['recovery_launch_id']}}}
    worker.create=create
    worker.inspect=lambda execution:copy.deepcopy(raw[0])
    def start(self,marker):
        calls.append('start');raw[0]['State'].update(Status='running',Running=True)
        return {'launch_id':marker,'identity':process}
    monkeypatch.setattr(RecoveryRuntime,'start',start)
    def post(path,body):
        assert not worker.start_lock.locked()
        calls.append('prepare')
        return {'execution_id':execution,**body,'action':'launch','reason':None,'duplicate':False,'valid_for_seconds':30}
    worker.api=SimpleNamespace(post=post)
    return worker,raw,calls,process


def test_create_confirm_attempt_bind_once(case):
    worker,raw,calls,process=case
    bound=prepare(worker,clock=lambda:10)
    assert calls==['create','prepare','start'] and bound['phase']=='bound'
    assert worker.state['recovery']=={'generation':'g','identity':process}
    assert json.loads(worker.path.read_text())==worker.state
    again=prepare(worker,clock=lambda:1000)
    assert again==bound and calls==['create','prepare','start']


def test_lost_start_response_never_starts_again(case,monkeypatch):
    worker,raw,calls,_=case;start=RecoveryRuntime.start
    def lose(self,marker):
        start(self,marker);raise TimeoutError('lost start response')
    monkeypatch.setattr(RecoveryRuntime,'start',lose)
    with pytest.raises(TimeoutError):prepare(worker,clock=lambda:10)
    assert json.loads(Path(str(worker.path)+'.launch.json').read_text())['phase']=='attempted'
    worker.state=json.loads(worker.path.read_text())
    assert prepare(worker,clock=lambda:100)['phase']=='bound'
    assert calls.count('start')==1


def test_parent_proof_write_failure_recovers_bound_runtime(case,monkeypatch):
    worker,_,calls,_=case
    def fail(path,state):
        if state.get('recovery'):raise OSError('proof write failed')
        atomic_json(path,state)
    monkeypatch.setattr('worker.recovery_start.atomic_json',fail)
    with pytest.raises(OSError):prepare(worker,clock=lambda:10)
    assert 'recovery' not in worker.state
    monkeypatch.setattr('worker.recovery_start.atomic_json',atomic_json)
    prepare(worker,clock=lambda:100)
    assert calls.count('start')==1


def test_creation_outcome_unknown_retains_intent(case):
    worker,_,calls,_=case
    worker.create=lambda task:(_ for _ in ()).throw(OSError('create outcome unknown'))
    with pytest.raises(OSError):prepare(worker,clock=lambda:10)
    with pytest.raises(RuntimeError,match='creation outcome unknown'):prepare(worker,clock=lambda:20)
    assert worker.state['created'] is True and calls==[]


def test_lost_creation_response_adopts_created_object(case):
    worker,_,calls,_=case;create=worker.create
    def lose(task):create(task);raise TimeoutError('created response lost')
    worker.create=lose
    with pytest.raises(TimeoutError):prepare(worker,clock=lambda:10)
    prepare(worker,clock=lambda:20)
    assert calls==['create','prepare','start']


@pytest.mark.parametrize('action',['stop','inspect'])
def test_cancel_or_conflict_before_start_blocks_launch(case,action):
    worker,_,calls,_=case;post=worker.api.post
    worker.api.post=lambda path,body:{**post(path,body),'action':action}
    with pytest.raises(RuntimeError,match='not authorized'):prepare(worker,clock=lambda:10)
    with pytest.raises(RuntimeError,match='launch blocked'):prepare(worker,clock=lambda:20)
    assert calls==['create','prepare']


def test_lost_prepare_response_does_not_extend_original_window(case):
    worker,_,calls,_=case;post=worker.api.post
    def lose(path,body):post(path,body);raise TimeoutError('response lost')
    worker.api.post=lose
    with pytest.raises(TimeoutError):prepare(worker,clock=lambda:10)
    worker.api.post=post
    with pytest.raises(RuntimeError,match='not authorized'):prepare(worker,clock=lambda:41)
    assert 'start' not in calls


@pytest.mark.parametrize('fault',['assignment','task','slot','boot','marker','proof'])
def test_mutated_assignment_refused(case,fault):
    worker,_,calls,_=case
    if fault in ('marker','proof'):prepare(worker,clock=lambda:10)
    if fault=='assignment':worker.state.pop('recovery_assignment')
    elif fault=='task':worker.state['task']['extra']='changed'
    elif fault=='slot':worker.slot_id='other'
    elif fault=='boot':worker.state['recovery_assignment']['claim']['boot_id']='other'
    elif fault=='marker':worker.state['task']['recovery_launch_id']=str(uuid.uuid4())
    else:worker.state['recovery']['generation']='other'
    before=list(calls)
    with pytest.raises((RuntimeError,ValueError)):prepare(worker,clock=lambda:20)
    assert calls==before


def test_preexisting_running_runtime_without_attempt_refused(case):
    worker,raw,calls,_=case;create=worker.create
    def running(task):create(task);raw[0]['State']['Status']='running'
    worker.create=running
    with pytest.raises(RuntimeError,match='without durable launch attempt'):prepare(worker,clock=lambda:10)
    assert calls==['create']


def test_reused_runtime_marker_refused(case):
    worker,raw,calls,_=case;create=worker.create
    def mismatch(task):create(task);raw[0]['Config']['Labels']['mini-eval.launch']=str(uuid.uuid4())
    worker.create=mismatch
    with pytest.raises(RuntimeError,match='marker missing or mismatched'):prepare(worker,clock=lambda:10)
    assert calls==['create']


def test_proof_without_attempt_cannot_start_again(case):
    worker,_,calls,process=case
    worker.state['recovery']={'generation':'g','identity':process}
    with pytest.raises(ValueError,match='without a durable launch attempt'):prepare(worker,clock=lambda:10)
    assert calls==[]


@pytest.mark.skipif(__import__('os').getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker startup opt-in')
@pytest.mark.parametrize('mode',['host','docker'])
def test_real_startup_response_loss_and_restart(tmp_path,monkeypatch,mode):
    import os,time
    from backend.tests.test_host_backend import worker as fixture_worker
    from worker.client import Worker,docker
    from worker.host_backend import HostBackend
    worker=fixture_worker(tmp_path,['python3','business.py'],timeout=0)
    worker.host_mode=lambda:mode=='host';worker.slot_id='s';worker.start_lock=threading.Lock()
    task=copy.deepcopy(worker.state['task']);execution=task['execution_id'];name='mini-eval-'+execution
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    worker.state.update(created=False,recovery_assignment={'reconciliation_required':True,
        'claim':{'slot_id':'s','boot_id':boot,'receipt':{'request_id':str(uuid.uuid4()),'execution_id':execution,
        'generation':'g','disposition':'reconcile','task':task}}})
    host=HostBackend(worker);calls=[]
    script='import time;print("start once",flush=True);time.sleep(.2);print("done",flush=True)'
    def create(task):
        calls.append('create')
        if mode=='host':
            (tmp_path/'business.py').write_text(script);host.create(task)
            path=host.root/'launch.json';launch=json.loads(path.read_text())
            launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(launch))
        else:
            docker('create','--name',name,'--label','mini-eval.execution='+execution,
                '--label','mini-eval.launch='+task['recovery_launch_id'],'--entrypoint','python',
                os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
    worker.create=create;worker.name=lambda e:'mini-eval-'+e;worker.inspect=lambda e:Worker.inspect(worker,e)
    def post(path,body):
        calls.append('prepare')
        return {'execution_id':execution,**body,'action':'launch','reason':None,'duplicate':False,'valid_for_seconds':30}
    worker.api=SimpleNamespace(post=post)
    original=RecoveryRuntime.start
    def lost(self,marker):
        observed=original(self,marker);calls.append(observed);raise TimeoutError('runtime started but reply lost')
    monkeypatch.setattr(RecoveryRuntime,'start',lost)
    try:
        with pytest.raises(TimeoutError):Worker.start_recovery(worker)
        worker.state=json.loads(worker.path.read_text())
        bound=Worker.start_recovery(worker)
        assert bound['identity']==calls[-1]['identity']
        assert len(calls)==3
        runtime=RecoveryRuntime(worker,execution);end=time.monotonic()+8
        while time.monotonic()<end:
            if runtime.inspect()['state']=='exited':break
            time.sleep(.05)
        assert runtime.inspect()['exit_code']==0
        assert Worker.start_recovery(worker)==bound
        assert len(calls)==3
    finally:
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)
