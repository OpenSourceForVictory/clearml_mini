"""Real local persistence and deterministic clocks; not a network E2E claim."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import stat
import sqlite3
import subprocess
import sys

import pytest

from worker.disconnection import DisconnectionClock
from worker.reliable_outbox import ReliableOutbox, OutboxConflict, OutboxFull


def clock(tolerance=10):
    return DisconnectionClock('execution', 'generation', 'boot', tolerance, 100)


def confirm(c, **overrides):
    return c.confirm(**dict(execution_id='execution', generation='generation',
                           sequence=1, requested_at=103, received_at=104, **overrides))


@pytest.mark.parametrize('tolerance', [0, -1, True, '10', 1.5, math.inf])
def test_invalid_tolerance(tolerance):
    with pytest.raises(ValueError):
        clock(tolerance)


@pytest.mark.parametrize('value', [-1, True, '100', math.nan, math.inf])
def test_invalid_monotonic_time(value):
    with pytest.raises(ValueError):
        DisconnectionClock('e', 'g', 'b', 10, value)


def test_finite_boundary_late_ack_cannot_resurrect():
    c = clock()
    assert c.check(109.999) is None
    assert c.check(110) == 'disconnection_timeout'
    assert not c.confirm(execution_id='execution', generation='generation', sequence=1,
                         requested_at=109, received_at=110)
    assert c.confirmed_at == 100
    assert c.check(1000) == 'disconnection_timeout'


def test_unlimited_retains_original_execution_after_long_disconnect():
    c = clock(None)
    assert c.check(10**9) is None
    assert c.confirm(execution_id='execution', generation='generation', sequence=1,
                     requested_at=10**9, received_at=10**9 + 1)


def test_confirmed_request_time_not_response_time():
    c = clock()
    assert confirm(c)
    assert c.confirmed_at == 103
    assert c.check(113) == 'disconnection_timeout'


@pytest.mark.parametrize('changes', [dict(execution_id='other'), dict(generation='other'),
                                    dict(sequence=0), dict(requested_at=99)])
def test_foreign_stale_ack_does_not_extend_clock(changes):
    c = clock()
    args = dict(execution_id='execution', generation='generation', sequence=1,
                requested_at=103, received_at=104)
    args.update(changes)
    assert not c.confirm(**args)
    assert c.confirmed_at == 100


def test_restart_preserves_clock_and_stop_decision(tmp_path):
    c = clock()
    assert confirm(c)
    path = tmp_path/'clock.json'
    path.write_text(json.dumps(c.snapshot()))
    resumed = DisconnectionClock.restore(json.loads(path.read_text()),
        execution_id='execution', generation='generation', boot_id='boot')
    assert resumed.check(112) is None
    assert resumed.check(113) == 'disconnection_timeout'
    stopped = DisconnectionClock.restore(resumed.snapshot(), execution_id='execution',
                                         generation='generation', boot_id='boot')
    assert stopped.stop_reason == 'disconnection_timeout'


def test_machine_restart_never_reuses_monotonic_clock():
    c = DisconnectionClock.restore(clock(None).snapshot(), execution_id='execution',
                                   generation='generation', boot_id='new-boot')
    assert c.stop_reason == 'node_restarted'
    assert c.check(0) == 'node_restarted'


@pytest.mark.parametrize('changes', [dict(version=2), dict(sequence=-1),
                                    dict(stop_reason='success'), dict(generation='other')])
def test_corrupt_clock_rejected(changes):
    record = clock().snapshot()
    record.update(changes)
    with pytest.raises(ValueError):
        DisconnectionClock.restore(record, execution_id='execution',
                                   generation='generation', boot_id='boot')


def test_clock_validation():
    with pytest.raises(ValueError):
        DisconnectionClock('', 'g', 'b', 10, 0)
    with pytest.raises(ValueError):
        clock().check(99)
    with pytest.raises(ValueError):
        clock().confirm(execution_id='execution', generation='generation', sequence=1,
                        requested_at=104, received_at=103)


def put(q, sequence=1, kind='episode', body=None):
    return q.put('execution', 'generation', kind, sequence, body or {'status': 'COMPLETED'})


def test_reopen_replay_ack_and_conflict(tmp_path):
    q = ReliableOutbox(tmp_path)
    digest = put(q)
    q = ReliableOutbox(tmp_path)
    assert q.pending()[0]['digest'] == digest
    assert put(q) == digest  # Lost transport ACK: stable record, no duplicate.
    assert len(q.pending()) == 1
    assert q.acknowledge('execution', 'generation', 'episode', 1, digest)
    assert not q.acknowledge('execution', 'generation', 'episode', 1, digest)
    assert not q.pending()
    assert put(q) == digest and not q.pending()
    with pytest.raises(OutboxConflict):
        put(q, body={'status': 'ERROR'})
    assert os.stat(q.path).st_mode & 0o777 == 0o600


def test_control_first_and_multiexecution_identity(tmp_path):
    q = ReliableOutbox(tmp_path)
    put(q)
    q.put('other', 'g2', 'task_result', 1, {'ok': True})
    put(q, kind='control', body={'exit_code': 0})
    rows = q.pending()
    assert [r['kind'] for r in rows] == ['control', 'episode', 'task_result']
    assert rows[2]['execution_id'] == 'other'
    with pytest.raises(OutboxConflict):
        q.put('execution', 'wrong-generation', 'episode', 2, {})
    with pytest.raises(OutboxConflict):
        q.acknowledge('execution', 'wrong-generation', 'episode', 1, rows[1]['digest'])
    with pytest.raises(OutboxConflict):
        q.acknowledge('execution', 'generation', 'episode', 1, 'wrong-digest')


def test_reserve_control_capacity_and_no_silent_loss(tmp_path):
    q = ReliableOutbox(tmp_path, max_records=3, control_reserve_records=1)
    put(q, 1); put(q, 2)
    with pytest.raises(OutboxFull):
        put(q, 3)
    put(q, kind='control')
    with pytest.raises(OutboxFull):
        put(q, 2, kind='control')
    assert len(q.pending()) == 3
    with pytest.raises(OutboxConflict):
        q.forget_confirmed_execution('execution', 'generation')


def test_ack_tombstones_remain_bounded_until_explicit_retention(tmp_path):
    q = ReliableOutbox(tmp_path, max_records=2, control_reserve_records=1)
    digest = put(q)
    q.acknowledge('execution', 'generation', 'episode', 1, digest)
    with pytest.raises(OutboxFull):
        put(q, 2)
    q.forget_confirmed_execution('execution', 'generation')
    put(q, 2)


def test_byte_limit_counts_utf8_and_retains_pending(tmp_path):
    q = ReliableOutbox(tmp_path, max_bytes=80, control_reserve_bytes=30)
    put(q, body={'text': '中'*10})  # 41 bytes.
    with pytest.raises(OutboxFull):
        put(q, 2, body={'text': '中'*10})
    put(q, kind='control', body={'exit': 0})
    assert len(ReliableOutbox(tmp_path).pending()) == 2


def test_single_record_limit_and_invalid_body(tmp_path):
    q = ReliableOutbox(tmp_path, max_record_bytes=10)
    with pytest.raises(OutboxFull):
        put(q)
    for body in ([], {'x': math.nan}):
        with pytest.raises(ValueError):
            q.put('e', 'g', 'episode', 1, body)
    assert q.pending() == []


@pytest.mark.parametrize('kwargs', [dict(max_bytes=0), dict(max_records=True),
    dict(control_reserve_bytes=64*1024*1024), dict(control_reserve_records=10000)])
def test_invalid_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        ReliableOutbox(tmp_path, **kwargs)


@pytest.mark.parametrize('key', [('', 'g', 'episode', 1), ('e','g','logs',1),
                                ('e','g','episode',True), ('e','g','episode',2**63)])
def test_invalid_message_key(tmp_path, key):
    with pytest.raises(ValueError):
        ReliableOutbox(tmp_path).put(*key, {})


def test_retention_requires_matching_identity_and_all_acks(tmp_path):
    q = ReliableOutbox(tmp_path)
    digest = put(q)
    with pytest.raises(OutboxConflict):
        q.forget_confirmed_execution('execution', 'wrong')
    q.acknowledge('execution', 'generation', 'episode', 1, digest)
    assert q.forget_confirmed_execution('execution', 'generation')
    assert not q.forget_confirmed_execution('execution', 'generation')
    assert not q.pending()
    with pytest.raises(ValueError):
        q.pending(1001)


def test_parallel_producers_and_duplicate_acks(tmp_path):
    q = ReliableOutbox(tmp_path)
    def produce(i):
        return put(ReliableOutbox(tmp_path), i)
    with ThreadPoolExecutor(max_workers=8) as pool:
        digests = list(pool.map(produce, list(range(20))*2))
    assert len(q.pending()) == 20
    def ack(_):
        return q.acknowledge('execution', 'generation', 'episode', 0, digests[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(ack, range(8))) == 1
    assert len(q.pending()) == 19


def test_pending_read_does_not_compete_for_writer_lock(tmp_path):
    q = ReliableOutbox(tmp_path)
    put(q)
    with sqlite3.connect(q.path) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE messages SET payload=NULL,size=0')
        assert len(q.pending()) == 1  # Last committed snapshot, no write lock.


def test_disk_failure_is_not_an_ack(tmp_path):
    q = ReliableOutbox(tmp_path)
    put(q)
    with sqlite3.connect(q.path) as db:
        db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'disk unavailable'); END")
    with pytest.raises(sqlite3.DatabaseError):
        put(q, 2)
    assert len(ReliableOutbox(tmp_path).pending()) == 1


def test_committed_record_survives_abrupt_process_exit(tmp_path):
    script = '''
import os, sys
from worker.reliable_outbox import ReliableOutbox
q = ReliableOutbox(sys.argv[1])
q.put('execution', 'generation', 'control', 1, {'exit_code': 0})
os._exit(23)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], timeout=10)
    assert result.returncode == 23
    assert ReliableOutbox(tmp_path).pending()[0]['body'] == {'exit_code': 0}


def test_uncommitted_write_rolls_back_after_process_exit(tmp_path):
    q = ReliableOutbox(tmp_path)
    put(q)
    script = '''
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('BEGIN IMMEDIATE')
db.execute('DELETE FROM messages')
os._exit(24)
'''
    result = subprocess.run([sys.executable, '-c', script, str(q.path)], timeout=10)
    assert result.returncode == 24
    assert len(ReliableOutbox(tmp_path).pending()) == 1


def test_symlink_store_rejected(tmp_path):
    target = tmp_path/'real'
    target.mkdir()
    link = tmp_path/'link'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        ReliableOutbox(link)
    (target/'reliable.sqlite3').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError):
        ReliableOutbox(target)


@pytest.mark.parametrize('fail_directory_sync', [False, True])
def test_worker_journal_commits_directory_and_surfaces_failure(tmp_path, monkeypatch, fail_directory_sync):
    from worker.client import Worker
    worker = Worker.__new__(Worker)
    worker.path = tmp_path/'journal.json'
    worker.state = {'task': {'execution_id': 'original'}, 'created': True}
    calls = []
    original = os.fsync
    def sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        calls.append(directory)
        if directory and fail_directory_sync:
            raise OSError('directory fsync failed')
        original(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    if fail_directory_sync:
        with pytest.raises(OSError, match='directory fsync'):
            worker.save()
    else:
        worker.save()
    assert calls == [False, True]
    assert json.loads(worker.path.read_text()) == worker.state
    assert not worker.path.with_suffix('.tmp').exists()


def test_compaction_retains_unacked_and_rejects_old_cursor_after_restart(tmp_path):
    q = ReliableOutbox(tmp_path, max_records=4, control_reserve_records=1)
    first = put(q, 1)
    second = put(q, 2)
    third = put(q, 3)
    q.acknowledge('execution','generation','episode',2,second)
    assert q.compact_confirmed('execution','generation',before=2)==0
    assert q.compact_confirmed('execution','generation',before=3)==1
    q = ReliableOutbox(tmp_path, max_records=4, control_reserve_records=1)
    assert [r['sequence'] for r in q.pending()]==[1,3]
    assert put(q,1)==first
    with pytest.raises(OutboxConflict,match='retirement'):
        put(q,2)
    with pytest.raises(OutboxConflict,match='generation'):
        q.compact_confirmed('execution','wrong',before=4)
    put(q,4)
    with pytest.raises(OutboxFull):put(q,5)
    q.acknowledge('execution','generation','episode',1,first)
    assert q.compact_confirmed('execution','generation',before=4)==1
    # Retiring a lower hole must not move the persisted boundary backwards.
    with pytest.raises(OutboxConflict):put(q,2)
    assert q.acknowledge('execution','generation','episode',3,third)
    assert q.compact_confirmed('execution','generation',before=4)==1


@pytest.mark.parametrize('cursor',[True,-1,2**63,'1'])
def test_compaction_rejects_invalid_cursor(tmp_path,cursor):
    with pytest.raises(ValueError):ReliableOutbox(tmp_path).compact_confirmed('e','g',before=cursor)


def test_compaction_empty_control_and_retention(tmp_path):
    q=ReliableOutbox(tmp_path)
    assert q.compact_confirmed('missing','g',before=10)==0
    control=put(q,1,kind='control')
    q.acknowledge('execution','generation','control',1,control)
    assert q.compact_confirmed('execution','generation',before=10)==0
    event=put(q,1)
    q.acknowledge('execution','generation','episode',1,event)
    assert q.compact_confirmed('execution','generation',before=10)==1
    assert q.compact_confirmed('execution','generation',before=10)==0
    q.forget_confirmed_execution('execution','generation')
    assert put(q,1)==event


def test_compaction_transaction_failure_rolls_back_boundary_and_rows(tmp_path):
    import sqlite3
    q=ReliableOutbox(tmp_path)
    digest=put(q,1)
    q.acknowledge('execution','generation','episode',1,digest)
    with sqlite3.connect(q.path) as db:
        db.execute("CREATE TRIGGER fail_retire BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        q.compact_confirmed('execution','generation',before=2)
    with sqlite3.connect(q.path) as db:
        assert db.execute('SELECT COUNT(*) FROM retired').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==1
        db.execute('DROP TRIGGER fail_retire')
    assert q.compact_confirmed('execution','generation',before=2)==1


def test_parallel_ack_and_compaction_preserves_every_pending_record(tmp_path):
    q=ReliableOutbox(tmp_path)
    digests=[put(q,i) for i in range(40)]
    def confirm(i):
        q.acknowledge('execution','generation','episode',i,digests[i])
    def compact(_):
        q.compact_confirmed('execution','generation',before=40)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(confirm,i) for i in range(20)]
        futures += [pool.submit(compact,i) for i in range(20)]
        for future in futures:future.result(timeout=10)
    q.compact_confirmed('execution','generation',before=40)
    assert [r['sequence'] for r in q.pending()]==list(range(20,40))
    for i in range(20,40):assert put(q,i)==digests[i]


def test_old_queue_adds_retirement_table_without_changing_pending(tmp_path):
    import sqlite3
    q=ReliableOutbox(tmp_path)
    put(q,1)
    pending=q.pending()
    with sqlite3.connect(q.path) as db:db.execute('DROP TABLE retired')
    reopened=ReliableOutbox(tmp_path)
    assert reopened.pending()==pending
    assert reopened.compact_confirmed('execution','generation',before=2)==0
