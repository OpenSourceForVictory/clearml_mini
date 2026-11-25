import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from worker.client import Worker
from worker.recovery_claim import RecoveryClaim
from worker.recovery_poll import poll, CAPABILITY
from worker.storage import atomic_json


@pytest.fixture
def case(tmp_path):
    task={'execution_id':str(uuid.uuid4()),'lease_seconds':30}
    calls=[]
    worker=SimpleNamespace(path=tmp_path/'worker.json',slot_id='slot',children={})
    def child(execution):
        path=tmp_path/(execution+'.json')
        return SimpleNamespace(path=path,state=json.loads(path.read_text()) if path.exists() else None,
            config={},runtime_snapshot={'revision':1},storage_snapshot={'revision':1})
    worker.child=child
    def post(path,body):
        calls.append((path,copy.deepcopy(body)))
        receipt={'request_id':body['request_id'],'execution_id':task['execution_id'],'generation':'generation',
            'policy':{'version':1,'disconnect_tolerance_seconds':None},'launch_authorized':False,
            'digest':hashlib.sha256(json.dumps(task,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'disposition':'reconcile','task':copy.deepcopy(task)}
        return {'request_id':body['request_id'],'task':copy.deepcopy(task),'recovery_claim':receipt,'cleanup':[]}
    worker.api=SimpleNamespace(post=post)
    return worker,task,calls


SOFTWARE={'capabilities':[CAPABILITY]}


def test_handoff_persists_before_recovery_dispatch_and_blocks_legacy_watchdog(case,monkeypatch):
    worker,task,calls=case
    assert poll(worker,SOFTWARE)['task']==task
    child=worker.children[task['execution_id']]
    assert child.state==json.loads(child.path.read_text())
    assert child.state['recovery_assignment']['reconciliation_required'] is True
    assert json.loads(Path(str(worker.path)+'.claim.json').read_text()) is None
    dispatched=[]
    monkeypatch.setattr('worker.recovery_execution.execute',lambda value:dispatched.append(value))
    Worker.execute(child,task)
    assert dispatched==[child]
    assert len(calls)==1


def test_lost_claim_response_reuses_request_even_without_advertisement(case):
    worker,task,calls=case;post=worker.api.post
    def lose(path,body):
        post(path,body)
        raise TimeoutError('lost response')
    worker.api.post=lose
    with pytest.raises(TimeoutError):poll(worker,SOFTWARE)
    worker.api.post=post
    poll(worker,{'capabilities':[]})
    assert calls[0]==calls[1]
    assert task['execution_id'] in worker.children


def test_handoff_replay_preserves_progress_after_claim_retirement_failure(case,monkeypatch):
    worker,task,calls=case;save=RecoveryClaim._save
    def fail(self,value):
        if value is None:raise OSError('claim retirement failed')
        return save(self,value)
    monkeypatch.setattr(RecoveryClaim,'_save',fail)
    with pytest.raises(OSError):poll(worker,SOFTWARE)
    child=worker.child(task['execution_id']);child.state['offset']=123
    atomic_json(child.path,child.state)
    monkeypatch.setattr(RecoveryClaim,'_save',save)
    poll(worker,SOFTWARE)
    assert worker.children[task['execution_id']].state['offset']==123
    assert calls[0]==calls[1]


def test_failed_child_persistence_keeps_claim_and_does_not_dispatch(case,monkeypatch):
    worker,task,_=case
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr('worker.recovery_poll.atomic_json',fail)
    with pytest.raises(OSError):poll(worker,SOFTWARE)
    assert worker.children=={}
    assert worker.child(task['execution_id']).state is None
    assert json.loads(Path(str(worker.path)+'.claim.json').read_text())['receipt']['task']==task


def test_conflicting_existing_journal_is_not_overwritten(case):
    worker,task,_=case
    child=worker.child(task['execution_id']);atomic_json(child.path,{'task':task,'created':True})
    with pytest.raises(ValueError,match='conflicts'):poll(worker,SOFTWARE)
    assert worker.child(task['execution_id']).state=={'task':task,'created':True}
    assert worker.children=={}


@pytest.mark.parametrize('fault',['request_id','missing_receipt','missing_task','cleanup_type','missing_assignment_receipt','task_mismatch','cleanup_with_receipt','receipt_type'])
def test_invalid_ack_keeps_pending_request(case,fault):
    worker,_,_=case;post=worker.api.post
    def invalid(path,body):
        value=post(path,body)
        if fault=='request_id':value['request_id']='other'
        elif fault=='missing_receipt':value.pop('recovery_claim')
        elif fault=='missing_task':value.pop('task')
        elif fault=='cleanup_type':value['cleanup']=None
        elif fault=='missing_assignment_receipt':value['recovery_claim']=None
        elif fault=='task_mismatch':value['task']={}
        elif fault=='cleanup_with_receipt':value['cleanup']=['other']
        else:value['recovery_claim']=[]
        return value
    worker.api.post=invalid
    with pytest.raises(ValueError):poll(worker,SOFTWARE)
    assert worker.children=={}
    assert json.loads(Path(str(worker.path)+'.claim.json').read_text()) is not None


def test_stop_receipt_is_retained_without_legacy_cleanup_or_launch(case):
    worker,_,calls=case;post=worker.api.post
    def stopped(path,body):
        value=post(path,body);value['task']=value['recovery_claim']['task']=None
        value['recovery_claim']['disposition']='stop'
        return value
    worker.api.post=stopped
    with pytest.raises(RuntimeError,match='stop reconciliation'):poll(worker,SOFTWARE)
    assert worker.children=={} and len(calls)==1
    assert json.loads(Path(str(worker.path)+'.claim.json').read_text())['receipt']['disposition']=='stop'


def test_explicit_empty_ack_clears_only_original_request(case):
    worker,_,_=case
    worker.api.post=lambda path,body:dict(request_id=body['request_id'],task=None,cleanup=[],recovery_claim=None)
    assert poll(worker,SOFTWARE)['task'] is None
    assert json.loads(Path(str(worker.path)+'.claim.json').read_text()) is None


def test_legacy_poll_preserves_wire_contract(case):
    worker,_,calls=case
    def post(path,body):
        calls.append(body);return {'task':None,'cleanup':['old']}
    worker.api.post=post
    assert poll(worker,{})=={'task':None,'cleanup':['old']}
    assert calls==[{'known_execution_ids':[]}]
    assert not Path(str(worker.path)+'.claim.json').exists()
    worker.api.post=lambda *args:{'task':{},'cleanup':[],'recovery_claim':{}}
    with pytest.raises(RuntimeError,match='unexpected recovery assignment'):poll(worker,{})
