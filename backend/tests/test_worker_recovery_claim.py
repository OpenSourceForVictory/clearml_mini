import copy
import hashlib
import json

import pytest

from worker.recovery_claim import RecoveryClaim


@pytest.fixture
def journal(tmp_path):
    value=RecoveryClaim(tmp_path/'claim')
    value.begin(slot_id='s',known_execution_ids=['b','a','b'],requested_at=1,boot_id='boot')
    yield value
    value.close()


def receipt(journal,**changes):
    task={'execution_id':'e','run_id':'r','job_id':'j'}
    digest=hashlib.sha256(json.dumps(task,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'request_id':journal.record['request']['request_id'],'execution_id':'e','generation':'g',
        'policy':{'version':1,'disconnect_tolerance_seconds':None},'digest':digest,
        'launch_authorized':False,'disposition':'reconcile','task':task,**changes}


def test_request_and_origin_survive_restart_and_new_boot(journal):
    path=journal.path;original=copy.deepcopy(journal.record)
    assert original['request']['known_execution_ids']==['a','b']
    journal.close();new=RecoveryClaim(path)
    try:
        assert new.begin(slot_id='s',known_execution_ids=[],requested_at=500,boot_id='new')==original['request']
        assert new.record==original
        with pytest.raises(ValueError,match='different slot'):new.begin(slot_id='other',known_execution_ids=[],requested_at=501,boot_id='new')
    finally:new.close()


def test_handoff_failure_preserves_receipt_and_empty_ack_cannot_clear_it(journal):
    message=receipt(journal);journal.accept(message);saved=[]
    message['task']['job_id']='mutated outside'
    def broken(record):
        saved.append(record);raise OSError('target journal unavailable')
    with pytest.raises(OSError):journal.handoff(broken)
    assert journal.record['receipt']['task']['job_id']=='j'
    with pytest.raises(ValueError):journal.empty(journal.record['request']['request_id'])
    journal.handoff(saved.append)
    assert saved[0]==saved[1] and saved[0]['reconciliation_required']
    assert json.loads(journal.path.read_text()) is None
    with pytest.raises(ValueError):journal.handoff(saved.append)
    with pytest.raises(ValueError):journal.accept(message)


def test_clear_failure_after_durable_handoff_replays_same_assignment(journal,monkeypatch):
    import worker.recovery_claim as module
    journal.accept(receipt(journal));saved={};calls=[]
    def persist(record):
        execution=record['claim']['receipt']['execution_id']
        if execution in saved:assert saved[execution]==record
        saved[execution]=record;calls.append(execution)
    real=module.atomic_json
    monkeypatch.setattr(module,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('fsync')))
    with pytest.raises(OSError):journal.handoff(persist)
    monkeypatch.setattr(module,'atomic_json',real)
    journal.handoff(persist)
    assert calls==['e','e'] and len(saved)==1


@pytest.mark.parametrize('changes',[
    {'request_id':'other'},{'launch_authorized':True},{'execution_id':''},{'generation':None},
    {'digest':'bad'},{'digest':'a'*64}, {'disposition':'start'}, {'task':None},
    {'task':{'execution_id':'other'}}, {'policy':{}}, {'policy':{'version':True,'disconnect_tolerance_seconds':None}},
    {'policy':{'version':1,'disconnect_tolerance_seconds':0}},
    {'policy':{'version':1,'disconnect_tolerance_seconds':True}},
    {'disposition':'stop'},
])
def test_invalid_receipt_never_replaces_pending_request(journal,changes):
    before=copy.deepcopy(journal.record)
    with pytest.raises(ValueError):journal.accept(receipt(journal,**changes))
    assert journal.record==before


def test_replayed_assignment_is_immutable_and_stop_cannot_be_reversed(journal):
    first=receipt(journal);journal.accept(first);journal.accept(first)
    for changes in ({'generation':'other'},{'policy':{'version':1,'disconnect_tolerance_seconds':60}}):
        with pytest.raises(ValueError,match='immutable'):journal.accept({**first,**changes})
    journal.accept({**first,'disposition':'stop','task':None})
    with pytest.raises(ValueError,match='restored'):journal.accept(first)
    saved=[];journal.handoff(saved.append)
    assert saved[0]['claim']['receipt']['task'] is None


def test_empty_response_allows_next_poll_with_new_request(journal):
    request=journal.record['request']['request_id']
    with pytest.raises(ValueError):journal.empty('other')
    journal.empty(request)
    with pytest.raises(ValueError):journal.empty(request)
    next_request=journal.begin(slot_id='s',known_execution_ids=[],requested_at=5,boot_id='boot')
    assert next_request['request_id']!=request and journal.record['requested_at']==5


@pytest.mark.parametrize('changes',[{'slot_id':''},{'boot_id':None},{'requested_at':-1},{'requested_at':float('inf')},
    {'requested_at':True},{'known_execution_ids':None},{'known_execution_ids':['']},{'known_execution_ids':['x']*1025}])
def test_invalid_initial_request(tmp_path,changes):
    journal=RecoveryClaim(tmp_path/'claim')
    try:
        with pytest.raises(ValueError):journal.begin(**{'slot_id':'s','boot_id':'boot','requested_at':1,'known_execution_ids':[],**changes})
        assert journal.record is None and not journal.path.exists()
    finally:journal.close()


def test_exclusive_owner_closed_owner_and_symlink(journal,tmp_path):
    with pytest.raises(BlockingIOError):RecoveryClaim(journal.path)
    journal.close()
    with pytest.raises(RuntimeError):journal.begin(slot_id='s',known_execution_ids=[],requested_at=1,boot_id='boot')
    (tmp_path/'link').symlink_to(journal.path)
    with pytest.raises(ValueError):RecoveryClaim(tmp_path/'link')
    (tmp_path/'bad').write_text('{"version":2}')
    with pytest.raises(ValueError):RecoveryClaim(tmp_path/'bad')


def test_failed_initial_persistence_never_returns_request(tmp_path,monkeypatch):
    import worker.recovery_claim as module
    journal=RecoveryClaim(tmp_path/'claim')
    monkeypatch.setattr(module,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    try:
        with pytest.raises(OSError):journal.begin(slot_id='s',known_execution_ids=[],requested_at=1,boot_id='boot')
        assert journal.record is None
    finally:journal.close()


def test_invalid_receipt_shape_and_oversized_task(journal):
    for value in (None,{}, {**receipt(journal),'extra':1}):
        with pytest.raises(ValueError):journal.accept(value)
    with pytest.raises(ValueError):journal.accept(receipt(journal,task={'execution_id':'e','large':'a'*(1024*1024)}))
