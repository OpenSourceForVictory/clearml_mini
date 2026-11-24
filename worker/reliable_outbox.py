"""Durable, bounded control/result records for recovery-protocol delivery.

Logs retain their existing files/cursors. This queue is deliberately separate
so log volume cannot consume the final control-record reserve. Network delivery
and authorization belong to the Worker/API integration, not this store.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3


class OutboxFull(RuntimeError):
    pass


class OutboxConflict(RuntimeError):
    pass


class ReliableOutbox:
    KINDS = ('control', 'episode', 'task_result')

    def __init__(self, directory, *, max_bytes=64 * 1024 * 1024,
                 max_records=10000, control_reserve_bytes=65536,
                 control_reserve_records=16, max_record_bytes=1048576):
        limits = (max_bytes, max_records, control_reserve_bytes,
                  control_reserve_records, max_record_bytes)
        if any(type(v) is not int or v <= 0 for v in limits):
            raise ValueError('outbox limits must be positive integers')
        if control_reserve_bytes >= max_bytes or control_reserve_records >= max_records:
            raise ValueError('control reserve must leave room for results')
        self.max_bytes, self.max_records = max_bytes, max_records
        self.reserve_bytes, self.reserve_records = control_reserve_bytes, control_reserve_records
        self.max_record_bytes = max_record_bytes
        directory = Path(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / 'reliable.sqlite3'
        if directory.is_symlink() or self.path.is_symlink():
            raise ValueError('outbox path must not be a symlink')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS executions (execution TEXT PRIMARY KEY, generation TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, execution TEXT NOT NULL, kind TEXT NOT NULL,
                sequence INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT,
                size INTEGER NOT NULL, UNIQUE(execution, kind, sequence))''')

    @contextmanager
    def _transaction(self, *, write=True):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, execution, generation, kind, sequence, body):
        if not all(isinstance(v, str) and 0 < len(v) <= 128 for v in (execution, generation)):
            raise ValueError('execution identity required')
        if kind not in self.KINDS or type(sequence) is not int or not 0 <= sequence < 2**63:
            raise ValueError('invalid reliable record key')
        if not isinstance(body, dict):
            raise ValueError('reliable record must be an object')
        payload = json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        encoded = payload.encode('utf-8')
        digest = hashlib.sha256(encoded).hexdigest()
        with self._transaction() as db:
            owner = db.execute('SELECT generation FROM executions WHERE execution=?', (execution,)).fetchone()
            if owner and owner['generation'] != generation:
                raise OutboxConflict('execution generation changed')
            old = db.execute('SELECT digest FROM messages WHERE execution=? AND kind=? AND sequence=?',
                             (execution, kind, sequence)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise OutboxConflict('reliable record changed for the same sequence')
                return digest
            if len(encoded) > self.max_record_bytes:
                raise OutboxFull('reliable record exceeds single-record limit')
            # ACK tombstones consume record slots too. Otherwise a long-lived
            # execution could grow SQLite without bound despite empty payloads.
            usage = db.execute('SELECT COUNT(*) AS count, COALESCE(SUM(size),0) AS bytes FROM messages').fetchone()
            byte_limit, count_limit = self.max_bytes, self.max_records
            if kind != 'control':
                byte_limit -= self.reserve_bytes
                count_limit -= self.reserve_records
            if usage['bytes'] + len(encoded) > byte_limit or usage['count'] + 1 > count_limit:
                raise OutboxFull('reliable outbox capacity reached; record not accepted')
            db.execute('INSERT OR IGNORE INTO executions VALUES (?,?)', (execution, generation))
            db.execute('INSERT INTO messages (execution,kind,sequence,digest,payload,size) VALUES (?,?,?,?,?,?)',
                       (execution, kind, sequence, digest, payload, len(encoded)))
        return digest

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid outbox page limit')
        with self._transaction(write=False) as db:
            rows = db.execute('''SELECT m.*, e.generation FROM messages m JOIN executions e USING(execution)
                WHERE payload IS NOT NULL ORDER BY (kind='control') DESC, id LIMIT ?''', (limit,)).fetchall()
        return [{'execution_id': row['execution'], 'generation': row['generation'],
                 'kind': row['kind'], 'sequence': row['sequence'], 'digest': row['digest'],
                 'body': json.loads(row['payload'])} for row in rows]

    def acknowledge(self, execution, generation, kind, sequence, digest):
        with self._transaction() as db:
            row = db.execute('''SELECT m.digest, m.payload FROM messages m JOIN executions e USING(execution)
                WHERE execution=? AND generation=? AND kind=? AND sequence=?''',
                             (execution, generation, kind, sequence)).fetchone()
            if not row or row['digest'] != digest:
                raise OutboxConflict('ACK does not match the original record')
            if row['payload'] is None:
                return False
            # Retain a digest tombstone: a lost ACK/re-enqueue cannot resurrect
            # the same event or silently replace it with different content.
            db.execute('UPDATE messages SET payload=NULL,size=0 WHERE execution=? AND kind=? AND sequence=?',
                       (execution, kind, sequence))
            return True

    def forget_confirmed_execution(self, execution, generation):
        """Explicit post-reconciliation retention, never an offline-age expiry."""
        with self._transaction() as db:
            row = db.execute('SELECT generation FROM executions WHERE execution=?', (execution,)).fetchone()
            if not row:
                return False
            if row['generation'] != generation:
                raise OutboxConflict('execution generation changed')
            if db.execute('SELECT 1 FROM messages WHERE execution=? AND payload IS NOT NULL LIMIT 1', (execution,)).fetchone():
                raise OutboxConflict('unconfirmed reliable records must be retained')
            db.execute('DELETE FROM messages WHERE execution=?', (execution,))
            db.execute('DELETE FROM executions WHERE execution=?', (execution,))
            return True
