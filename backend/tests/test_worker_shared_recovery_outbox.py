"""Multi-card result capacity and per-execution delivery stay independent."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.recovery_delivery import RecoveryDelivery
from worker.recovery_finish import finish_results
from worker.recovery_results import execution_outbox
from worker.reliable_outbox import OutboxFull, ReliableOutbox


def worker(tmp_path, name, execution):
    path = tmp_path / f'{name}.json.runs' / f'{execution}.json'
    path.parent.mkdir(exist_ok=True)
    state = {'task': {'execution_id': execution}}
    path.write_text(json.dumps(state))
    return SimpleNamespace(path=path, state=state,
        config={'node_name':'same-node', 'node_outbox_root':str(tmp_path/'shared')})


def test_multicard_workers_share_node_budget_but_not_delivery(tmp_path):
    first = worker(tmp_path, 'gpu0', 'first')
    second = worker(tmp_path, 'gpu1', 'second')
    q1 = execution_outbox(first)
    q2 = execution_outbox(second)
    assert q1.path == q2.path
    assert first.state['recovery_outbox_dir'] == second.state['recovery_outbox_dir']
    q1.put('first', 'g1', 'episode', 1, {'status':'STARTED'})
    q2.put('second', 'g2', 'episode', 1, {'status':'STARTED'})
    calls = []
    def post(path, body):
        calls.append(path)
        return {'digest':q1.pending(execution='first')[0]['digest']}
    assert RecoveryDelivery(SimpleNamespace(post=post), q1, execution_id='first').step() == 1
    assert calls == ['worker/executions/first/recovery/results']
    assert [row['execution_id'] for row in q2.pending()] == ['second']
    assert execution_outbox(first, existing=True).path == q1.path


def test_node_and_execution_limits_are_atomic(tmp_path):
    q = ReliableOutbox(tmp_path, max_bytes=200, max_records=5,
        control_reserve_bytes=10, control_reserve_records=1,
        max_execution_bytes=100, max_execution_records=3)
    for number in (1, 2):
        q.put('a', 'g', 'episode', number, {'value':number})
    with pytest.raises(OutboxFull, match='execution'):
        q.put('a', 'g', 'episode', 3, {'value':3})
    q.put('b', 'g', 'episode', 1, {'value':1})
    q.put('b', 'g', 'episode', 2, {'value':2})
    with pytest.raises(OutboxFull, match='outbox'):
        q.put('c', 'g', 'episode', 1, {'value':1})
    q.put('a', 'g', 'control', 1, {'state':'exited'})
    assert len(q.pending()) == 5
    assert len(ReliableOutbox(tmp_path).pending()) == 5


def test_execution_byte_cap_preserves_other_execution_and_control_space(tmp_path):
    q = ReliableOutbox(tmp_path, max_bytes=200, max_records=10,
        control_reserve_bytes=10, control_reserve_records=1,
        max_execution_bytes=60, max_execution_records=5)
    q.put('a', 'g', 'episode', 1, {'value':'x'*20})
    with pytest.raises(OutboxFull, match='execution'):
        q.put('a', 'g', 'episode', 2, {'value':'x'*20})
    q.put('b', 'g', 'episode', 1, {'value':'x'*20})
    q.put('a', 'g', 'control', 1, {'state':'done'})
    assert len(q.pending()) == 3


def test_concurrent_cards_cannot_exceed_shared_capacity(tmp_path):
    root = tmp_path/'shared'
    ReliableOutbox(root, max_records=5, control_reserve_records=1)
    def produce(index):
        queue = ReliableOutbox(root, max_records=5, control_reserve_records=1)
        try:
            queue.put(f'card-{index}', 'g', 'episode', 1, {'value':index})
            return True
        except OutboxFull:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(produce, range(16))) == 4
    assert len(ReliableOutbox(root).pending()) == 4


def test_result_finish_ignores_other_execution_backlog(tmp_path):
    q = ReliableOutbox(tmp_path/'shared')
    q.put('other', 'g', 'episode', 1, {'status':'STARTED'})
    digest = q.put('mine', 'g', 'task_result', 1, {'success':True})
    q.acknowledge('mine', 'g', 'task_result', 1, digest)
    proof = {'generation':'g', 'identity':{}}
    state = {'task':{'execution_id':'mine'}, 'recovery':proof,
        'recovery_finalization':{'proof':proof, 'phase':'done',
                                 'body':{'pending_results':True}},
        'recovery_results_collected':True}
    path = tmp_path/'mine.json'
    path.write_text(json.dumps(state))
    calls = []
    def post(endpoint, body):
        calls.append(endpoint)
        return {'result_sync':'complete', 'cleanup_confirmed':True,
                'status':'SUCCEEDED', 'duplicate':False}
    w = SimpleNamespace(state=state, path=path, api=SimpleNamespace(post=post))
    assert finish_results(w, q)
    assert calls == ['worker/executions/mine/recovery/results-finished']
    assert len(q.pending()) == 1
    assert q.forget_confirmed_execution('mine', 'g') is False


def test_retirement_disk_failure_keeps_finished_journal(tmp_path, monkeypatch):
    q = ReliableOutbox(tmp_path/'shared')
    digest = q.put('mine', 'g', 'task_result', 1, {'success':True})
    q.acknowledge('mine', 'g', 'task_result', 1, digest)
    proof = {'generation':'g', 'identity':{}}
    state = {'task':{'execution_id':'mine'}, 'recovery':proof,
        'recovery_finalization':{'proof':proof, 'phase':'done',
                                 'body':{'pending_results':True}},
        'recovery_results_collected':True}
    path = tmp_path/'mine.json'
    path.write_text(json.dumps(state))
    w = SimpleNamespace(state=state, path=path,
        api=SimpleNamespace(post=lambda *_:{'result_sync':'complete',
            'cleanup_confirmed':True, 'status':'SUCCEEDED', 'duplicate':False}))
    monkeypatch.setattr(q, 'forget_confirmed_execution',
                        lambda *_: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        finish_results(w, q)
    assert json.loads(path.read_text()) == state


def test_missing_pinned_queue_and_legacy_queue_are_not_replaced(tmp_path):
    w = worker(tmp_path, 'gpu0', 'first')
    q = execution_outbox(w)
    q.put('first', 'g', 'episode', 1, {'value':1})
    w.state = json.loads(w.path.read_text())
    assert execution_outbox(w, existing=True).pending(execution='first')
    Path(q.path).unlink()
    with pytest.raises(RuntimeError, match='missing'):
        execution_outbox(w, existing=True)
    old = worker(tmp_path, 'gpu1', 'old')
    legacy = ReliableOutbox(Path(str(old.path)+'.outbox'))
    legacy.put('old', 'g', 'episode', 1, {'value':1})
    assert execution_outbox(old).path == legacy.path
    assert 'recovery_outbox_dir' not in old.state


@pytest.mark.parametrize('root_type', ['relative', 'symlink'])
def test_invalid_shared_root_is_rejected_before_queue_creation(tmp_path, root_type):
    w = worker(tmp_path, 'gpu0', 'first')
    w.config['node_outbox_root'] = 'relative/path'
    if root_type == 'symlink':
        # Existing symlink roots must also be rejected, including when they
        # happen to resolve to a writable directory.
        link = tmp_path/'link'
        link.symlink_to(tmp_path, target_is_directory=True)
        w.config['node_outbox_root'] = str(link)
    with pytest.raises(ValueError, match='root'):
        execution_outbox(w)
    assert 'recovery_outbox_dir' not in json.loads(w.path.read_text())


def test_new_negotiated_claim_requires_shared_root(tmp_path):
    from worker.recovery_poll import poll
    w = SimpleNamespace(path=tmp_path/'state.json', config={}, slot_id='slot',
                        api=SimpleNamespace(post=lambda *_:pytest.fail('must not claim')),
                        children={})
    with pytest.raises(ValueError, match='shared node outbox'):
        poll(w, {'capabilities':['disconnected_execution_v1']})


def test_new_queue_directory_sync_failure_never_advances_result_cursor(tmp_path, monkeypatch):
    from worker import reliable_outbox
    w = worker(tmp_path, 'gpu0', 'first')
    original = reliable_outbox.os.fsync
    def fail(fd):
        if str(tmp_path/'shared') in reliable_outbox.os.readlink(f'/proc/self/fd/{fd}'):
            raise OSError('directory sync failed')
        return original(fd)
    monkeypatch.setattr(reliable_outbox.os, 'fsync', fail)
    with pytest.raises(OSError, match='directory sync'):
        execution_outbox(w)
    assert 'recovery_outbox_dir' in json.loads(w.path.read_text())
    assert 'episode_offset' not in json.loads(w.path.read_text())
    monkeypatch.setattr(reliable_outbox.os, 'fsync', original)
    assert execution_outbox(w, existing=True).pending(execution='first') == []
