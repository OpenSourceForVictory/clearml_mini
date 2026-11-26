"""Bounded v2 logs. The log cursor is serialized independently of renewals."""
import hashlib
import json
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, delete, text
from .core import ExecutionRecovery, Run, RunLog, RunLogChunk, RunLogState, Slot, now
from .scheduling import TERMINAL, utc

WINDOW_BYTES = 262144
CHUNK_BYTES = 16384


class LogChunk(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence: int = Field(ge=0, le=2**63-2, strict=True)
    content: str = Field(max_length=CHUNK_BYTES)
    stream: Literal['stdout', 'stderr', 'system'] = 'stdout'
    gap: bool = False

    @model_validator(mode='after')
    def bounded(self):
        if not self.content and not self.gap:
            raise ValueError('empty log chunk')
        if '\x00' in self.content or len(self.content.encode('utf-8')) > CHUNK_BYTES:
            raise ValueError('invalid or oversized UTF-8 log chunk')
        return self


def digest(body):
    return hashlib.sha256(json.dumps(body.model_dump(), ensure_ascii=True,
                                    sort_keys=True).encode()).hexdigest()


def terminal_log_lock(s, run_id):
    """Serialize v2 log writes with terminal transitions for this run only."""
    if s.bind.dialect.name == 'postgresql':
        # Negative namespace cannot alias the positive global scheduling key.
        key = (int.from_bytes(hashlib.sha256(('mini-eval-log:' + run_id).encode()).digest()[:8],
                              'big') | (1 << 63)) - (1 << 64)
        s.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})


def append(s, execution_id, worker, body):
    # KEY SHARE prevents deletion without blocking a heartbeat's NO KEY UPDATE.
    # The per-run advisory lock orders append against complete/cleanup, including
    # the first chunk when RunLogState does not exist yet.
    run = s.scalar(select(Run).where(Run.execution_id == execution_id)
                   .with_for_update(read=True, key_share=True))
    if not run:
        raise HTTPException(404, 'execution not found')
    slot = s.get(Slot, run.slot_id)
    if not slot or slot.worker_id != worker.id:
        raise HTTPException(404, 'execution not found')
    terminal_log_lock(s, run.id)
    s.refresh(run)
    # A negotiated execution may keep running while disconnected. Its lease
    # expires, but the immutable recovery grant still owns its log tail until
    # the platform commits a terminal state. Ordinary executions retain the
    # strict lease check.
    lease_valid = bool(run.lease_expires_at and utc(run.lease_expires_at) > now())
    recovery = s.get(ExecutionRecovery, execution_id) if not lease_valid else None
    recoverable = bool(recovery and recovery.run_id == run.id
                       and recovery.slot_id == run.slot_id
                       and recovery.worker_id == worker.id and not recovery.revoked)
    if run.status not in ('STARTING', 'RUNNING', 'CANCEL_REQUESTED') or not (lease_valid or recoverable):
        raise HTTPException(409, 'execution lease is no longer valid')
    state = s.scalar(select(RunLogState).where(RunLogState.run_id == run.id).with_for_update())
    if not state:
        state = RunLogState(run_id=run.id, next_sequence=0, first_sequence=0,
                            retained_bytes=0, truncated=False)
        s.add(state)
    checksum = digest(body)
    if body.sequence < state.next_sequence:
        previous = s.get(RunLogChunk, (run.id, body.sequence))
        known = previous.digest if previous else state.last_digest if body.sequence == state.next_sequence-1 else None
        if known is None:
            return {'accepted': False, 'resync': True, 'next_sequence': state.next_sequence}
        if known != checksum:
            raise HTTPException(409, 'log sequence already used')
        return {'accepted': True, 'next_sequence': state.next_sequence}
    if body.sequence != state.next_sequence:
        raise HTTPException(409, 'log sequence gap')
    size = len(body.content.encode('utf-8'))
    s.add(RunLogChunk(run_id=run.id, sequence=body.sequence, content=body.content,
                      stream=body.stream, gap=body.gap, size_bytes=size, digest=checksum))
    state.next_sequence += 1
    state.retained_bytes += size
    state.last_digest = checksum
    state.truncated = state.truncated or body.gap
    s.flush()
    # Also cap empty/gap records and tiny chunks, so metadata is bounded too.
    if state.retained_bytes > WINDOW_BYTES or state.next_sequence-state.first_sequence > 256:
        rows = s.execute(select(RunLogChunk.sequence, RunLogChunk.size_bytes).where(
            RunLogChunk.run_id == run.id).order_by(RunLogChunk.sequence)).all()
        remove = []
        for sequence, size_bytes in rows:
            if state.retained_bytes <= WINDOW_BYTES and state.next_sequence-state.first_sequence <= 256:
                break
            state.retained_bytes -= size_bytes
            state.first_sequence = sequence+1
            state.truncated = True
            remove.append(sequence)
        if remove:
            s.execute(delete(RunLogChunk).where(RunLogChunk.run_id == run.id,
                                                RunLogChunk.sequence.in_(remove)))
    return {'accepted': True, 'next_sequence': state.next_sequence}


def read(s, run, after=-1, limit=100):
    state = s.get(RunLogState, run.id)
    model = RunLogChunk if state else RunLog
    rows = s.scalars(select(model).where(model.run_id == run.id, model.sequence > after)
                     .order_by(model.sequence).limit(limit)).all()
    first = state.first_sequence if state else 0
    cursor = rows[-1].sequence if rows else after
    complete = run.status in TERMINAL
    if complete:
        complete = s.scalar(select(model.sequence).where(model.run_id == run.id, model.sequence > cursor).limit(1)) is None
    return {'run_id': run.id, 'version': 2 if state else 1,
            'chunks': [{'sequence': r.sequence, 'content': r.content,
                        'stream': r.stream if state else 'stdout',
                        'gap': r.gap if state else False} for r in rows],
            'next_after': rows[-1].sequence if rows else after,
            'first_sequence': first, 'gap': after+1 < first,
            'truncated': state.truncated if state else run.log_bytes >= WINDOW_BYTES,
            'complete': complete,
            'max_bytes': WINDOW_BYTES}


def install(app, db, worker_auth):
    @app.post('/api/v1/worker/executions/{execution_id}/logs-v2')
    def upload(execution_id: str, body: LogChunk, worker=Depends(worker_auth), s=Depends(db)):
        result = append(s, execution_id, worker, body)
        s.commit()
        return result
