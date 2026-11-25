import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from worker.client import Worker
from worker.reliable_outbox import ReliableOutbox,OutboxFull,OutboxConflict
from worker.storage import atomic_json
from worker.workspace import execution_dirs


EVENT={'episode_id':1,'seed':100,'status':'STARTED'}


@pytest.fixture
def case(tmp_path):
    w=object.__new__(Worker);execution=str(uuid.uuid4())
    w.path=tmp_path/'worker.json';w.workspace=tmp_path/'workspace'
    (w.workspace/'runs').mkdir(parents=True)
    w.state={'task':{'execution_id':execution,'benchmark':'cs-client',
        'client_spec':{'template':{'integration_mode':'direct-protocol','episode_reporting':True}}},
        'recovery_assignment':{'claim':{'receipt':{'execution_id':execution,'generation':'g'}}}}
    w.api=SimpleNamespace(post=lambda *a:pytest.fail('collection must never use HTTP'))
    root=tmp_path/'protocol';root.mkdir()
    outbox=ReliableOutbox(tmp_path/'outbox')
    atomic_json(w.path,w.state)
    return w,outbox,root


def source(case,mode,event=EVENT):
    w,_,root=case;task=w.state['task'];template=task['client_spec']['template']
    work=execution_dirs(w.workspace,task['execution_id'])/'work';work.mkdir(parents=True,exist_ok=True)
    line=json.dumps(event)+'\n'
    if mode=='protocol':task['log_protocol']=2;(root/'events.jsonl').write_text(line);return {'protocol_root':root},'protocol_event_offset',line
    if mode=='journal':(work/'episodes.jsonl').write_text(line);return {},'episode_file_offset',line
    if mode=='stdout':template['episode_reporting']=False;return {'output':'noise\nMINI_EVAL_EVENT '+line},'episode_offset',line
    template.update(integration_mode='sdk-task-result',task_type='command')
    (work/'task-result.json').write_text(json.dumps({'success':True,'metrics':{'score':1}}))
    return {},'task_result_sha256',line


@pytest.mark.parametrize('mode',['protocol','journal','stdout','task'])
def test_all_protocol_readers_queue_before_advancing_and_do_not_use_http(case,mode):
    w,outbox,_=case;args,cursor,_=source(case,mode)
    assert w.collect_recovery_results(outbox,**args)
    records=outbox.pending();assert len(records)==1
    assert records[0]['kind']==('task_result' if mode=='task' else 'episode')
    assert w.state[cursor] and json.loads(w.path.read_text())==w.state
    assert w.collect_recovery_results(outbox,**args)
    assert outbox.pending()==records


@pytest.mark.parametrize('mode',['protocol','journal','stdout','task'])
def test_cursor_write_failure_replays_same_outbox_key(case,monkeypatch,mode):
    w,outbox,_=case;args,cursor,_=source(case,mode)
    def fail(path,state):
        if state.get(cursor) and (mode!='stdout' or state[cursor]>len('noise\n')):raise OSError('cursor commit failed')
        atomic_json(path,state)
    monkeypatch.setattr('worker.recovery_results.atomic_json',fail)
    with pytest.raises(OSError):w.collect_recovery_results(outbox,**args)
    assert w.state.get(cursor,0)==(len('noise\n') if mode=='stdout' else 0)
    records=outbox.pending();assert len(records)==1
    w.state=json.loads(w.path.read_text())
    monkeypatch.setattr('worker.recovery_results.atomic_json',atomic_json)
    assert w.collect_recovery_results(outbox,**args)
    assert outbox.pending()==records


@pytest.mark.parametrize('mode',['protocol','journal','stdout','task'])
def test_outbox_full_does_not_advance_or_drop_results(case,monkeypatch,mode):
    w,outbox,_=case;args,cursor,_=source(case,mode)
    def full(*args):raise OutboxFull('full')
    monkeypatch.setattr(outbox,'put',full)
    with pytest.raises(OutboxFull):w.collect_recovery_results(outbox,**args)
    # stdout's leading ordinary line may advance; the result itself cannot.
    assert w.state.get(cursor,0)==(len('noise\n') if mode=='stdout' else 0)
    assert outbox.pending()==[]


def test_partial_line_waits_and_batch_limit_preserves_tail(case):
    w,outbox,root=case;args,cursor,line=source(case,'protocol')
    events=''.join(json.dumps({**EVENT,'episode_id':i})+'\n' for i in range(1,19))
    (root/'events.jsonl').write_text(events+'{"episode_id":')
    assert not w.collect_recovery_results(outbox,**args)
    assert len(outbox.pending())==16
    assert not w.collect_recovery_results(outbox,**args)
    assert len(outbox.pending())==18
    assert w.state[cursor]==len(events.encode())


def test_changed_result_source_or_assignment_refused(case):
    w,outbox,_=case;args,_,_=source(case,'protocol')
    w.collect_recovery_results(outbox,**args)
    w.state['task'].pop('log_protocol')
    with pytest.raises(ValueError,match='source'):w.collect_recovery_results(outbox)
    w.state['recovery_assignment']['claim']['receipt']['execution_id']='different'
    with pytest.raises(ValueError,match='assignment'):w.collect_recovery_results(outbox)


def test_missing_assignment_or_protocol_root(case):
    w,outbox,_=case
    w.state['task']['log_protocol']=2
    with pytest.raises(ValueError,match='root'):w.collect_recovery_results(outbox)
    w.state={}
    with pytest.raises(ValueError,match='assignment'):w.collect_recovery_results(outbox)


def test_services_have_no_client_result_reader(case):
    w,outbox,_=case;w.state['task']['benchmark']='inference-service'
    assert w.collect_recovery_results(outbox) and outbox.pending()==[]


def test_modified_same_offset_cannot_overwrite_durable_result(case,monkeypatch):
    w,outbox,root=case;args,cursor,_=source(case,'protocol')
    def fail(path,state):
        if state.get(cursor):raise OSError('cursor save failed')
        atomic_json(path,state)
    monkeypatch.setattr('worker.recovery_results.atomic_json',fail)
    with pytest.raises(OSError):w.collect_recovery_results(outbox,**args)
    (root/'events.jsonl').write_text(json.dumps({**EVENT,'seed':101})+'\n')
    with pytest.raises(OutboxConflict):w.collect_recovery_results(outbox,**args)
    assert outbox.pending()[0]['body']==EVENT


def test_reader_cannot_send_non_result_requests(case,monkeypatch):
    w,outbox,_=case;args,_,_=source(case,'task')
    monkeypatch.setattr(Worker,'send_task_result',lambda self,e:self.api.post('worker/heartbeat',{}))
    with pytest.raises(ValueError,match='unrelated'):w.collect_recovery_results(outbox,**args)


@pytest.mark.parametrize('mode',['protocol','journal','stdout','task','service'])
def test_final_collection_requires_exit_and_seals_only_after_durable_read(case,monkeypatch,mode):
    w,q,root=case
    args,_,_=source(case,'protocol' if mode=='service' else mode)
    if mode=='service':w.state['task']['benchmark']='inference-service'
    bound={'kind':'host','boot_id':'boot','runtime_id':'a'*32}
    w.state['recovery']={'generation':'g','identity':bound}
    observed={'state':'running','identity':bound,'exit_code':None}
    monkeypatch.setattr('worker.recovery_runtime.RecoveryRuntime.inspect',lambda self:observed)
    with pytest.raises(RuntimeError):w.collect_recovery_results(q,final=True,**args)
    assert not w.state.get('recovery_results_collected')
    observed.update(state='exited',exit_code=0)
    assert w.collect_recovery_results(q,final=True,**args)
    assert json.loads(w.path.read_text())['recovery_results_collected'] is True


@pytest.mark.parametrize('mode',['protocol','journal','stdout'])
def test_final_collection_does_not_seal_partial_record(case,monkeypatch,mode):
    w,q,root=case;args,_,_=source(case,mode)
    bound={'kind':'host','boot_id':'boot','runtime_id':'a'*32}
    w.state['recovery']={'identity':bound}
    monkeypatch.setattr('worker.recovery_runtime.RecoveryRuntime.inspect',
        lambda self:{'state':'exited','identity':bound,'exit_code':0})
    if mode=='stdout':args['output']+='MINI_EVAL_EVENT {"episode_id":'
    else:
        path=root/'events.jsonl' if mode=='protocol' else execution_dirs(w.workspace,w.state['task']['execution_id'])/'work'/'episodes.jsonl'
        with path.open('a') as f:f.write('{"episode_id":')
    assert not w.collect_recovery_results(q,final=True,**args)
    assert not w.state.get('recovery_results_collected')


def test_empty_queue_without_final_collection_cannot_retire_journal(tmp_path):
    from worker.recovery_finish import finish_results
    proof={'generation':'g','identity':{}}
    w=SimpleNamespace(state={'recovery':proof,'recovery_finalization':{
        'proof':proof,'phase':'done','body':{'pending_results':True}}},
        api=SimpleNamespace(post=lambda *a:pytest.fail('no sync before collection finishes')))
    assert not finish_results(w,ReliableOutbox(tmp_path/'empty'))
